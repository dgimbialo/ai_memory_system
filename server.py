#!/usr/bin/env python3
"""AI Memory System — local HTML dashboard server.

Usage:
    python server.py --project piobmasterpro [--port 5001] [--no-browser]

Serves the single-page UI from ui/ and exposes a JSON REST API at /api/*.
Pure Python stdlib — no external dependencies.
Binds to 127.0.0.1 only (localhost, no external access).

REST API:
    GET    /api/projects
    GET    /api/entries?project=X&type=&status=&q=&file=&tag=
    GET    /api/entry/<id>?project=X
    POST   /api/entry                        body: add_memory payload
    PATCH  /api/entry/<id>                   body: {status?, confidence?, tags?, reason?}
    GET    /api/conflicts?project=X
    POST   /api/conflicts/<id>/resolve       body: {action, reason}
    GET    /api/stats?project=X
    GET    /api/settings?project=X
    POST   /api/settings                     body: settings dict
    GET    /api/file_summary?project=X&file=F
    GET    /api/graph?project=X
    GET    /api/suggest_links/<id>?project=X
    POST   /api/ops/decay                    body: {dry_run, half_life_days, min_confidence}
    POST   /api/ops/deduplicate              body: {dry_run, threshold}
    POST   /api/ops/render_wiki
    POST   /api/ops/lint
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path, PurePath
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Force UTF-8 on Windows consoles (avoids cp1252 crash on → ✅ ⚠ etc.)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

UI_DIR = ROOT / "ui"
DATA_DIR = ROOT / "data"
SETTINGS_FILE = "settings.json"

from core.engine import MemoryEngine  # noqa: E402

# ---------------------------------------------------------------------------
# Default settings model
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS: dict = {
    "decay": {
        "enabled": True,
        "half_life_days": 60,
        "min_confidence": 0.40,
    },
    "deduplication": {
        "enabled": True,
        "threshold": 0.88,
    },
    "revert_detection": {
        "enabled": True,
        "threshold": 2,
    },
    "stale_check": {
        "min_age_days": 7,
    },
    "wiki": {
        "auto_render": True,
    },
    "query": {
        "top_k": 10,
        "decay_blend": 0.10,
    },
    "default_tags": ["agent", "auto"],
}

CONTENT_TYPES: dict = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".ico":  "image/x-icon",
    ".png":  "image/png",
    ".svg":  "image/svg+xml",
    ".woff2": "font/woff2",
}

# ---------------------------------------------------------------------------
# Request audit log — ring buffer + SSE fan-out
# ---------------------------------------------------------------------------

import collections, time as _time  # noqa: E402  (stdlib, always available)

_LOG_MAXLEN = 500           # keep last 500 events in memory
_log_ring: collections.deque = collections.deque(maxlen=_LOG_MAXLEN)
_log_lock = threading.Lock()
_sse_clients: list = []      # list of queue.Queue, one per open SSE connection

import queue as _queue       # noqa: E402


def _audit(source: str, method: str, path: str, body: dict,
           status: int, duration_ms: float, project: str | None = None) -> None:
    """Append one event to the ring buffer and fan-out to SSE subscribers."""
    event = {
        "ts":          datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "source":      source,         # "http" | "mcp" | "cli"
        "method":      method,
        "path":        path,
        "project":     project or "",
        "body":        body,
        "status":      status,
        "duration_ms": round(duration_ms, 1),
    }
    data = json.dumps(event, ensure_ascii=False, default=str)
    with _log_lock:
        _log_ring.append(event)
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(data)
            except _queue.Full:
                dead.append(q)
        for q in dead:
            try:
                _sse_clients.remove(q)
            except ValueError:
                pass

# ---------------------------------------------------------------------------
# Project helpers (mirror logic from run.py)
# ---------------------------------------------------------------------------

def _project_slug(project: str) -> str:
    slug = PurePath(project).name
    return slug.strip().lower().replace(" ", "_") or "default"


def _data_dir(project: str | None) -> Path:
    if project:
        p = Path(project)
        if p.is_absolute() and p.exists():
            return p
        return DATA_DIR / "projects" / _project_slug(project)
    return DATA_DIR


def _engine(project: str | None) -> MemoryEngine:
    return MemoryEngine(_data_dir(project))


def _list_projects() -> list:
    projects = []
    projects_dir = DATA_DIR / "projects"
    if projects_dir.exists():
        for d in sorted(projects_dir.iterdir()):
            if not d.is_dir():
                continue
            mem_file = d / "memory.json"
            count = 0
            if mem_file.exists():
                try:
                    data = json.loads(mem_file.read_text(encoding="utf-8"))
                    count = len(data) if isinstance(data, list) else 0
                except Exception:
                    pass
            projects.append({"name": d.name, "entry_count": count})
    # Include default project if it has data
    default_mem = DATA_DIR / "memory.json"
    if default_mem.exists():
        try:
            data = json.loads(default_mem.read_text(encoding="utf-8"))
            count = len(data) if isinstance(data, list) else 0
            if count > 0:
                projects.insert(0, {"name": "default", "entry_count": count})
        except Exception:
            pass
    return projects


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class MemoryHandler(BaseHTTPRequestHandler):
    default_project: str | None = None
    _lock = threading.Lock()

    def log_message(self, fmt, *args):  # suppress default access log
        pass

    # ── HTTP verbs ─────────────────────────────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path.startswith("/api/"):
            self._dispatch_api("GET", path, qs, {})
        else:
            self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        body = self._read_body()
        self._dispatch_api("POST", path, qs, body)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        body = self._read_body()
        self._dispatch_api("PATCH", path, qs, body)

    # ── router ─────────────────────────────────────────────────────────────

    def _dispatch_api(self, method: str, path: str, qs: dict, body: dict):
        _t0 = _time.perf_counter()
        _status_holder = [200]
        _orig_json = self._json  # patch to capture status

        def _tracked_json(data, status=200):
            _status_holder[0] = status
            _orig_json(data, status)

        self._json = _tracked_json  # type: ignore[method-assign]

        try:
            project = self._project(qs)
            seg = [s for s in path.split("/") if s]  # ['api', cmd, ...]

            # SSE stream endpoint — handle before the normal API routing
            if method == "GET" and path == "/api/log/stream":
                self._json = _orig_json  # restore before streaming
                self._sse_log_stream()
                _audit("http", method, path, {}, 200,
                       (_time.perf_counter() - _t0) * 1000, project)
                return

            # Log snapshot endpoint
            if method == "GET" and path == "/api/log/entries":
                with _log_lock:
                    snapshot = list(_log_ring)
                self._json(snapshot[-int(self._qs1(qs, "limit", "200")):])
                _audit("http", method, path, {}, _status_holder[0],
                       (_time.perf_counter() - _t0) * 1000, project)
                return

            if method == "GET":
                if path == "/api/projects":
                    self._json(_list_projects())
                elif path == "/api/entries":
                    self._api_entries(project, qs)
                elif len(seg) == 3 and seg[1] == "entry":
                    self._api_get_entry(seg[2], project)
                elif path == "/api/conflicts":
                    self._api_conflicts(project)
                elif path == "/api/stats":
                    self._api_stats(project)
                elif path == "/api/settings":
                    self._api_get_settings(project)
                elif path == "/api/file_summary":
                    self._api_file_summary(project, self._qs1(qs, "file", ""))
                elif path == "/api/graph":
                    self._api_graph(project)
                elif len(seg) == 3 and seg[1] == "suggest_links":
                    self._api_suggest_links(seg[2], project)
                else:
                    self._error("Not found", 404)

            elif method == "POST":
                if path == "/api/entry":
                    self._api_add_entry(project, body)
                elif path == "/api/settings":
                    self._api_post_settings(project, body)
                elif len(seg) == 4 and seg[1] == "conflicts" and seg[3] == "resolve":
                    self._api_resolve_conflict(seg[2], project, body)
                elif path == "/api/ops/decay":
                    self._api_op_decay(project, body)
                elif path == "/api/ops/deduplicate":
                    self._api_op_deduplicate(project, body)
                elif path == "/api/ops/render_wiki":
                    self._api_op_render_wiki(project)
                elif path == "/api/ops/lint":
                    self._api_op_lint(project)
                else:
                    self._error("Not found", 404)

            elif method == "PATCH":
                if len(seg) == 3 and seg[1] == "entry":
                    self._api_update_entry(seg[2], project, body)
                else:
                    self._error("Not found", 404)

        except Exception as exc:
            self._error(f"Internal error: {exc}", 500)
        finally:
            self._json = _orig_json  # always restore
            _audit("http", method, path,
                   {k: v for k, v in body.items() if k != "password"} if body else {},
                   _status_holder[0],
                   (_time.perf_counter() - _t0) * 1000,
                   self._project(qs))

    # ── API handlers ────────────────────────────────────────────────────────

    def _api_entries(self, project, qs: dict):
        eng = _engine(project)
        memory = eng._read_memory()

        type_   = self._qs1(qs, "type", "")
        status  = self._qs1(qs, "status", "")
        q       = self._qs1(qs, "q", "").lower()
        file_   = self._qs1(qs, "file", "").lower()
        tag     = self._qs1(qs, "tag", "").lower()

        results = []
        for e in memory:
            if type_   and e.get("type")   != type_:    continue
            if status  and e.get("status") != status:   continue
            if file_   and not any(file_ in f.lower() for f in e.get("files") or []):
                continue
            if tag and not any(tag in t.lower() for t in e.get("tags") or []):
                continue
            if q:
                text = " ".join(filter(None, [
                    e.get("description", ""), e.get("cause", ""),
                    e.get("fix", ""), " ".join(e.get("decisions") or []),
                ])).lower()
                if q not in text:
                    continue
            results.append(e)

        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        self._json(results)

    def _api_get_entry(self, entry_id: str, project):
        eng = _engine(project)
        for e in eng._read_memory():
            if e.get("id") == entry_id:
                self._json(e)
                return
        self._error(f"Entry {entry_id!r} not found", 404)

    def _api_add_entry(self, project, body: dict):
        eng = _engine(project)
        with self._lock:
            result = eng.add_memory(body)
        self._json(result, 201)

    def _api_update_entry(self, entry_id: str, project, body: dict):
        eng = _engine(project)
        result: dict = {}
        with self._lock:
            if "status" in body:
                result["status_result"] = eng.update_status(
                    entry_id, body["status"], body.get("reason", ""))
            if "confidence" in body:
                result["confidence_result"] = eng.update_confidence(
                    entry_id, float(body["confidence"]), body.get("reason", ""))
            if "tags" in body:
                memory = eng._read_memory()
                for e in memory:
                    if e.get("id") == entry_id:
                        e["tags"] = list(body["tags"])
                        eng.storage.write("memory.json", memory)
                        result["tags_result"] = {"updated": True, "tags": e["tags"]}
                        break
        self._json(result)

    def _api_conflicts(self, project):
        eng = _engine(project)
        self._json(eng.list_conflicts())

    def _api_resolve_conflict(self, conflict_id: str, project, body: dict):
        action = body.get("action", "")
        if action not in ("supersede_a", "supersede_b", "merge", "dismiss"):
            self._error("action must be supersede_a|supersede_b|merge|dismiss", 400)
            return
        eng = _engine(project)
        with self._lock:
            result = eng.resolve_conflict(conflict_id, action, body.get("reason", ""))
        self._json(result)

    def _api_stats(self, project):
        eng = _engine(project)
        memory = eng._read_memory()
        conflicts = eng._read_conflicts()

        by_type: dict = {}
        by_status: dict = {}
        total_conf = 0.0
        by_date: dict = {}
        file_counts: dict = {}
        tag_counts: dict = {}
        conf_buckets = [0] * 10  # 0.0–1.0 in tenths

        for e in memory:
            t = e.get("type", "note")
            by_type[t] = by_type.get(t, 0) + 1
            s = e.get("status", "active")
            by_status[s] = by_status.get(s, 0) + 1
            conf = float(e.get("confidence") or 0.5)
            total_conf += conf
            conf_buckets[min(int(conf * 10), 9)] += 1
            date = (e.get("timestamp") or "")[:10]
            if date:
                by_date[date] = by_date.get(date, 0) + 1
            for f in e.get("files") or []:
                file_counts[f] = file_counts.get(f, 0) + 1
            for tag in e.get("tags") or []:
                if not tag.startswith("project:"):
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1

        n = len(memory)
        avg_conf = round(total_conf / n, 3) if n else 0.0

        today = datetime.now(timezone.utc).date()
        entries_per_day = [
            {
                "date":  (today - timedelta(days=i)).isoformat(),
                "count": by_date.get((today - timedelta(days=i)).isoformat(), 0),
            }
            for i in range(29, -1, -1)
        ]

        top_files = sorted(file_counts.items(), key=lambda x: -x[1])[:10]
        top_tags  = sorted(tag_counts.items(),  key=lambda x: -x[1])[:15]
        open_conflicts = len([c for c in conflicts if c.get("status") != "resolved"])
        linked = sum(
            1 for e in memory
            if (e.get("depends_on") or e.get("required_by"))
        )

        self._json({
            "total":                 n,
            "by_type":               by_type,
            "by_status":             by_status,
            "avg_confidence":        avg_conf,
            "open_conflicts":        open_conflicts,
            "entries_per_day":       entries_per_day,
            "top_files":             [{"file": f, "count": c} for f, c in top_files],
            "confidence_histogram":  conf_buckets,
            "top_tags":              [{"tag": t, "count": c} for t, c in top_tags],
            "linked_entries":        linked,
        })

    def _api_get_settings(self, project):
        path = _data_dir(project) / SETTINGS_FILE
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                self._json(_deep_merge(DEFAULT_SETTINGS, saved))
                return
            except Exception:
                pass
        self._json(dict(DEFAULT_SETTINGS))

    def _api_post_settings(self, project, body: dict):
        data_dir = _data_dir(project)
        data_dir.mkdir(parents=True, exist_ok=True)
        merged = _deep_merge(DEFAULT_SETTINGS, body)
        path = data_dir / SETTINGS_FILE
        path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        self._json({"saved": True, "settings": merged})

    def _api_file_summary(self, project, file_path: str):
        if not file_path:
            self._error("file parameter required", 400)
            return
        eng = _engine(project)
        result = eng.summarize_file(file_path)
        memory = eng._read_memory()
        entries = sorted(
            [e for e in memory if file_path in (e.get("files") or [])],
            key=lambda x: x.get("timestamp", ""),
            reverse=True,
        )
        self._json({**result, "entries": entries})

    def _api_graph(self, project):
        eng = _engine(project)
        memory = eng._read_memory()

        linked_ids: set = set()
        for e in memory:
            if e.get("depends_on") or e.get("required_by"):
                linked_ids.add(e.get("id", ""))
                for d in e.get("depends_on") or []:
                    linked_ids.add(d)
                for r in e.get("required_by") or []:
                    linked_ids.add(r)

        nodes = []
        edges = []
        for e in memory:
            eid = e.get("id", "")
            if eid in linked_ids or e.get("type") == "decision":
                nodes.append({
                    "id":         eid,
                    "label":      (e.get("description") or "")[:55],
                    "type":       e.get("type", "note"),
                    "status":     e.get("status", "active"),
                    "confidence": float(e.get("confidence") or 0.5),
                    "files":      (e.get("files") or [])[:3],
                    "tags":       [t for t in (e.get("tags") or [])
                                   if not t.startswith("project:")],
                })
            for dep_id in (e.get("depends_on") or []):
                edges.append({"from": eid, "to": dep_id})

        self._json({"nodes": nodes, "edges": edges})

    def _api_suggest_links(self, entry_id: str, project):
        eng = _engine(project)
        try:
            suggestions = eng.suggest_links(entry_id)
            self._json(suggestions)
        except KeyError:
            self._error(f"Entry {entry_id!r} not found", 404)

    # ── Operations ──────────────────────────────────────────────────────────

    def _api_op_decay(self, project, body: dict):
        eng = _engine(project)
        with self._lock:
            result = eng.decay(
                dry_run=bool(body.get("dry_run", True)),
                half_life_days=float(body.get("half_life_days", 60)),
                min_confidence=float(body.get("min_confidence", 0.40)),
            )
        self._json(result)

    def _api_op_deduplicate(self, project, body: dict):
        eng = _engine(project)
        with self._lock:
            result = eng.deduplicate(
                dry_run=bool(body.get("dry_run", True)),
                threshold=float(body.get("threshold", 0.88)),
            )
        self._json(result)

    def _api_op_render_wiki(self, project):
        eng = _engine(project)
        with self._lock:
            result = eng.render_wiki_md()
        self._json(result)

    def _api_op_lint(self, project):
        from core.lint import Linter
        result = Linter(_data_dir(project)).run()
        self._json(result)

    # ── Static file serving ─────────────────────────────────────────────────

    def _sse_log_stream(self) -> None:
        """Server-Sent Events stream for the real-time log tab.

        Sends the current ring-buffer snapshot on connect, then fans out every
        new event. Stays open until the client disconnects (broken pipe).
        Each event is: ``data: <json>\\n\\n``.
        """
        q: _queue.Queue = _queue.Queue(maxsize=200)

        # Send current snapshot first
        with _log_lock:
            snapshot = list(_log_ring)
            _sse_clients.append(q)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            # Replay history to the new subscriber
            for event in snapshot:
                payload = ("data: " + json.dumps(event, ensure_ascii=False,
                                                   default=str) + "\n\n").encode("utf-8")
                self.wfile.write(payload)
            self.wfile.flush()
            # Stream live events
            while True:
                try:
                    data = q.get(timeout=20)
                    self.wfile.write(("data: " + data + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                except _queue.Empty:
                    # Keepalive comment to prevent proxy timeouts
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _log_lock:
                try:
                    _sse_clients.remove(q)
                except ValueError:
                    pass

    def _serve_static(self, path: str):
        if path in ("/", ""):
            path = "/index.html"
        rel = unquote(path).lstrip("/")
        target = (UI_DIR / rel).resolve()
        try:
            target.relative_to(UI_DIR.resolve())
        except ValueError:
            self.send_response(403)
            self.end_headers()
            return
        if not target.is_file():
            self.send_response(404)
            self.end_headers()
            return
        body = target.read_bytes()
        ct = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _project(self, qs: dict) -> str | None:
        return self._qs1(qs, "project", None) or self.__class__.default_project

    def _qs1(self, qs: dict, key: str, default):
        vals = qs.get(key)
        return vals[0] if vals else default

    def _json(self, data, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, msg: str, status: int = 400):
        self._json({"error": msg}, status)

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            return {}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def make_handler(project: str | None):
    class BoundHandler(MemoryHandler):
        default_project = project
    return BoundHandler


def main():
    parser = argparse.ArgumentParser(
        description="AI Memory System — local HTML dashboard server",
    )
    parser.add_argument("--project", "-p", default=None, metavar="NAME",
                        help="Default project (e.g. piobmasterpro)")
    parser.add_argument("--port", type=int, default=5001,
                        help="Listen port (default: 5001)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not open browser automatically")
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), make_handler(args.project))
    url = f"http://localhost:{args.port}"
    print(f"  AI Memory System  →  {url}")
    if args.project:
        print(f"  Default project   →  {args.project}")
    print("  Press Ctrl+C to stop.\n")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Server stopped.")


if __name__ == "__main__":
    main()
