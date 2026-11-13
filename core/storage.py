"""SQLite-backed storage layer (schema v1) behind the original Storage API.

Every project store is one ``memory.db`` file (WAL mode). The public API is
unchanged - ``read(name, default)``, ``write(name, data)``, ``append_log`` -
and still speaks in the five legacy document names, so the engine and every
caller keep working untouched:

    memory.json          -> table ``entries``   (one row per entry, diff-written)
    conflicts.json       -> table ``conflicts`` (one row per record, diff-written)
    activity_log.json    -> table ``events``    (append-only; O(1) append)
    wiki.json            -> table ``docs``
    file_summaries.json  -> table ``docs``

Why SQLite (audit #4): the JSON layout rewrote a 1 MB file plus a backup copy
on every operation, appended to the activity log by rewriting the whole log
(1.5 MB for one project), raced between four writer processes (MCP server,
hooks, CLI, dashboard) and kept a single-generation backup that could not undo
the September merge cascade. One transactional file fixes all four.

Legacy JSON files found next to the database are imported once and moved to
``legacy_json/``. A ``memory.json`` that reappears later (written by a process
still running pre-migration code) is merged in by entry id on the next open.

Names outside the five above keep the old behaviour: plain JSON files.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set

_LOCK = threading.RLock()

DB_NAME = "memory.db"
SCHEMA_VERSION = 1

_KEYED = {"memory.json": "entries", "conflicts.json": "conflicts"}
_LOG = "activity_log.json"
_DOCS = {"wiki.json", "file_summaries.json"}
_LEGACY_NAMES = ("memory.json", "conflicts.json", "activity_log.json",
                 "wiki.json", "file_summaries.json")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta      (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS entries   (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conflicts (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events    (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, action TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS docs      (name TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recall_seen (session_id TEXT, entry_id TEXT, ts TEXT,
                                        PRIMARY KEY (session_id, entry_id));
CREATE INDEX IF NOT EXISTS idx_entries_id   ON entries(id);
CREATE INDEX IF NOT EXISTS idx_conflicts_id ON conflicts(id);
CREATE INDEX IF NOT EXISTS idx_events_action_ts ON events(action, ts);
"""

# A memory write that deletes this share of a store is suspicious enough to
# snapshot first (the September cascade and its repair both rewrote in bulk).
_BULK_DELETE_MIN = 20
_BULK_DELETE_SHARE = 0.5


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


class Storage:
    def __init__(self, data_dir: str | os.PathLike):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / DB_NAME
        self.snapshot_dir = self.data_dir / ".snapshots"
        with _LOCK:
            self._init_db()

    # ── connection ───────────────────────────────────────────────────────────

    @contextmanager
    def _conn(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(str(self.db_path), timeout=15, isolation_level=None)
        try:
            con.execute("PRAGMA synchronous=NORMAL")
            if write:
                con.execute("BEGIN IMMEDIATE")
            yield con
            if write:
                con.execute("COMMIT")
        except Exception:
            if write:
                try:
                    con.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        finally:
            con.close()

    def _init_db(self) -> None:
        fresh = not self.db_path.exists()
        with self._conn() as con:
            if fresh:
                con.execute("PRAGMA journal_mode=WAL")
            con.executescript(_SCHEMA)
        self._import_legacy()

    # ── legacy JSON import / late-writer reconciliation ─────────────────────

    def _import_legacy(self) -> None:
        present = [n for n in _LEGACY_NAMES if (self.data_dir / n).exists()]
        if not present:
            if self._meta_get("schema_version") is None:
                with self._conn(write=True) as con:
                    con.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                                (str(SCHEMA_VERSION),))
            return
        moved: List[str] = []
        with self._conn(write=True) as con:
            # Decided inside the write lock, so two processes opening a legacy
            # store at once cannot both run the destructive first import.
            migrated = con.execute(
                "SELECT 1 FROM meta WHERE key='schema_version'").fetchone() is not None
            for name in present:
                try:
                    data = json.loads((self.data_dir / name).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not migrated:
                    self._write_in(con, name, data)
                    moved.append(name)
                elif name == "memory.json" and isinstance(data, list):
                    # A stale pre-migration process wrote JSON after the
                    # import: keep whatever it added, by entry id.
                    have = {r[0] for r in con.execute("SELECT id FROM entries")}
                    for item in data:
                        if isinstance(item, dict) and item.get("id") and item["id"] not in have:
                            con.execute("INSERT INTO entries(id, data) VALUES (?, ?)",
                                        (item["id"], _dumps(item)))
                    moved.append(name)
                else:
                    moved.append(name)
            con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                        (str(SCHEMA_VERSION),))
        legacy_dir = self.data_dir / "legacy_json"
        legacy_dir.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        for name in moved:
            src = self.data_dir / name
            dst = legacy_dir / (name if not migrated else f"{Path(name).stem}.late-{stamp}.json")
            try:
                if dst.exists():
                    dst = legacy_dir / f"{Path(name).stem}.{stamp}.json"
                shutil.move(str(src), str(dst))
            except OSError:
                pass  # leave it; it will be re-merged (idempotently) next time

    # ── public API (unchanged contract) ──────────────────────────────────────

    def path(self, name: str) -> Path:
        return self.data_dir / name

    def exists(self, name: str) -> bool:
        """Has this document ever been written? (Replaces path(name).exists().)"""
        if name in _KEYED or name == _LOG:
            return self._meta_get(f"init:{name}") is not None or self.count(name) > 0
        if name in _DOCS:
            with self._conn() as con:
                return con.execute("SELECT 1 FROM docs WHERE name=?", (name,)).fetchone() is not None
        return self.path(name).exists()

    def read(self, name: str, default: Any) -> Any:
        with _LOCK:
            if name in _KEYED:
                with self._conn() as con:
                    rows = con.execute(f"SELECT data FROM {_KEYED[name]} ORDER BY seq").fetchall()
                return [json.loads(r[0]) for r in rows] if rows else (
                    [] if self._meta_get(f"init:{name}") is not None else default)
            if name == _LOG:
                with self._conn() as con:
                    rows = con.execute("SELECT data FROM events ORDER BY seq").fetchall()
                return [json.loads(r[0]) for r in rows] if rows else (
                    [] if self._meta_get(f"init:{name}") is not None else default)
            if name in _DOCS:
                with self._conn() as con:
                    row = con.execute("SELECT data FROM docs WHERE name=?", (name,)).fetchone()
                return json.loads(row[0]) if row else default
            return self._read_file(name, default)

    def write(self, name: str, data: Any) -> None:
        with _LOCK:
            if name in _KEYED or name == _LOG or name in _DOCS:
                if name == "memory.json" and isinstance(data, list):
                    self._guard_bulk_delete(data)
                with self._conn(write=True) as con:
                    self._write_in(con, name, data)
                return
            self._write_file(name, data)

    def append_log(self, name: str, entry: dict) -> None:
        """Append one record to the activity log: a single INSERT."""
        with _LOCK:
            if name == _LOG:
                with self._conn(write=True) as con:
                    self._insert_event(con, entry)
                return
            current = self._read_file(name, [])
            if not isinstance(current, list):
                current = []
            current.append(entry)
            self._write_file(name, current)

    # ── extra queries the JSON layout could not offer cheaply ───────────────

    def count(self, name: str) -> int:
        table = _KEYED.get(name) or ("events" if name == _LOG else None)
        if table is None:
            return 0
        with self._conn() as con:
            return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def count_events(self, action: str, since_iso: str = "") -> int:
        with self._conn() as con:
            return int(con.execute(
                "SELECT COUNT(*) FROM events WHERE action=? AND ts>=?", (action, since_iso)
            ).fetchone()[0])

    def events_since(self, since_iso: str, actions: Optional[Iterable[str]] = None) -> List[dict]:
        q, args = "SELECT data FROM events WHERE ts>=?", [since_iso]
        if actions:
            acts = list(actions)
            q += " AND action IN (%s)" % ",".join("?" * len(acts))
            args += acts
        with self._conn() as con:
            return [json.loads(r[0]) for r in con.execute(q + " ORDER BY seq", args)]

    def seen_in_session(self, session_id: str) -> Set[str]:
        if not session_id:
            return set()
        with self._conn() as con:
            return {r[0] for r in con.execute(
                "SELECT entry_id FROM recall_seen WHERE session_id=?", (session_id,))}

    def mark_seen(self, session_id: str, entry_ids: Iterable[str]) -> None:
        if not session_id:
            return
        now = datetime.now(timezone.utc).isoformat()
        with _LOCK, self._conn(write=True) as con:
            con.executemany(
                "INSERT OR IGNORE INTO recall_seen(session_id, entry_id, ts) VALUES (?,?,?)",
                [(session_id, i, now) for i in entry_ids])
            # keep the table from growing without bound (sessions are short-lived)
            con.execute("DELETE FROM recall_seen WHERE ts < ?",
                        (datetime.fromtimestamp(time.time() - 14 * 86400, timezone.utc).isoformat(),))

    def meta_get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        v = self._meta_get(f"kv:{key}")
        return default if v is None else v

    def meta_set(self, key: str, value: str) -> None:
        with _LOCK, self._conn(write=True) as con:
            con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                        (f"kv:{key}", value))

    # ── snapshots / export ───────────────────────────────────────────────────

    def snapshot(self, label: str = "", keep: int = 14) -> Path:
        """Consistent copy of the whole store (SQLite backup API).

        Daily snapshots are named memory-YYYYMMDD.db and pruned to the newest
        ``keep``; labelled snapshots (memory-YYYYMMDD-<label>.db) are kept.
        """
        self.snapshot_dir.mkdir(exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        name = f"memory-{day}-{label}.db" if label else f"memory-{day}.db"
        dst = self.snapshot_dir / name
        with _LOCK:
            src = sqlite3.connect(str(self.db_path), timeout=15)
            try:
                out = sqlite3.connect(str(dst))
                try:
                    src.backup(out)
                finally:
                    out.close()
            finally:
                src.close()
        if not label:
            daily = sorted(p for p in self.snapshot_dir.glob("memory-????????.db"))
            for old in daily[:-keep]:
                try:
                    old.unlink()
                except OSError:
                    pass
        return dst

    def export_json(self, out_dir: str | os.PathLike) -> Path:
        """Write the five legacy JSON documents (portability, inspection)."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        for name, default in (("memory.json", []), ("conflicts.json", []),
                              ("activity_log.json", []), ("wiki.json", {}),
                              ("file_summaries.json", {})):
            (out / name).write_text(
                json.dumps(self.read(name, default), indent=2, ensure_ascii=False),
                encoding="utf-8")
        return out

    # ── internals ────────────────────────────────────────────────────────────

    def _meta_get(self, key: str) -> Optional[str]:
        with self._conn() as con:
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _guard_bulk_delete(self, new_items: list) -> None:
        with self._conn() as con:
            have = {r[0] for r in con.execute("SELECT id FROM entries")}
        if len(have) < _BULK_DELETE_MIN:
            return
        keep = {i.get("id") for i in new_items if isinstance(i, dict)}
        gone = len(have - keep)
        if gone >= max(_BULK_DELETE_MIN, _BULK_DELETE_SHARE * len(have)):
            try:
                self.snapshot(label="pre-bulk-delete-" + datetime.now(timezone.utc).strftime("%H%M%S"))
            except (sqlite3.Error, OSError):
                pass

    def _write_in(self, con: sqlite3.Connection, name: str, data: Any) -> None:
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, '1')", (f"init:{name}",))
        if name in _KEYED:
            self._write_keyed(con, _KEYED[name], data if isinstance(data, list) else [])
        elif name == _LOG:
            con.execute("DELETE FROM events")
            for item in (data if isinstance(data, list) else []):
                self._insert_event(con, item)
        else:
            con.execute("INSERT OR REPLACE INTO docs(name, data) VALUES (?, ?)", (name, _dumps(data)))

    @staticmethod
    def _insert_event(con: sqlite3.Connection, entry: Any) -> None:
        ts = action = ""
        if isinstance(entry, dict):
            ts = str(entry.get("timestamp") or "")
            action = str(entry.get("action") or "")
        con.execute("INSERT INTO events(ts, action, data) VALUES (?, ?, ?)", (ts, action, _dumps(entry)))

    @staticmethod
    def _write_keyed(con: sqlite3.Connection, table: str, items: List[Any]) -> None:
        """Make the table equal to ``items`` (order included), touching only
        the rows that changed. Falls back to a full rewrite when ids are
        missing/duplicated or existing rows were reordered."""
        ids = [i.get("id") if isinstance(i, dict) else None for i in items]
        diffable = all(ids) and len(set(ids)) == len(ids)
        existing = con.execute(f"SELECT id, data FROM {table} ORDER BY seq").fetchall()
        ex_ids = [r[0] for r in existing]
        if diffable and len(set(ex_ids)) == len(ex_ids) and all(ex_ids):
            new_set = set(ids)
            kept_order = [i for i in ex_ids if i in new_set]
            head = ids[:len(kept_order)]
            if head == kept_order:  # existing rows keep their order, new ones only at the tail
                ex_map = dict(existing)
                gone = [i for i in ex_ids if i not in new_set]
                if gone:
                    con.executemany(f"DELETE FROM {table} WHERE id=?", [(i,) for i in gone])
                for item, iid in zip(items, ids):
                    text = _dumps(item)
                    old = ex_map.get(iid)
                    if old is None:
                        con.execute(f"INSERT INTO {table}(id, data) VALUES (?, ?)", (iid, text))
                    elif old != text:
                        con.execute(f"UPDATE {table} SET data=? WHERE id=?", (text, iid))
                return
        con.execute(f"DELETE FROM {table}")
        con.executemany(
            f"INSERT INTO {table}(id, data) VALUES (?, ?)",
            [((i.get("id") if isinstance(i, dict) else None), _dumps(i)) for i in items])

    # ── plain-file fallback for names outside the store schema ──────────────

    def _read_file(self, name: str, default: Any) -> Any:
        p = self.path(name)
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _write_file(self, name: str, data: Any) -> None:
        target = self.path(name)
        fd, tmp_path = tempfile.mkstemp(prefix=f".{name}.", dir=str(self.data_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            for attempt in range(5):
                try:
                    os.replace(tmp_path, target)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise


# ── module-level helpers for read-only callers (hooks, dashboard, linter) ───

def has_store(data_dir: str | os.PathLike) -> bool:
    """True when the directory holds a store (migrated or still legacy JSON)."""
    d = Path(data_dir)
    return (d / DB_NAME).exists() or (d / "memory.json").exists()


def read_store(data_dir: str | os.PathLike, name: str, default: Any) -> Any:
    """Read one store document without creating a database in an empty dir."""
    if not has_store(data_dir):
        return default
    return Storage(data_dir).read(name, default)


def store_entry_count(data_dir: str | os.PathLike) -> int:
    if not has_store(data_dir):
        return 0
    try:
        return Storage(data_dir).count("memory.json")
    except sqlite3.Error:
        return 0
