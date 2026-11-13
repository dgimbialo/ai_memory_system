"""Memory Engine — single controlled gateway for all memory operations."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import MemoryEntry, ConflictRecord
from .storage import Storage
from .updater import Updater
from .conflict import find_conflicts_for, find_all_conflicts
from .embeddings import similarity, backend as embed_backend
from .wiki_md import WikiRenderer


MEMORY_FILE = "memory.json"
WIKI_FILE = "wiki.json"
CONFLICTS_FILE = "conflicts.json"
LOG_FILE = "activity_log.json"
FILE_SUMMARIES_FILE = "file_summaries.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Auto-tagging helpers
# ---------------------------------------------------------------------------

# Maps keyword substrings (lowercased) → tag to add when found in description/cause/fix.
# Order matters: first match wins per keyword group.
_KEYWORD_TAGS: List[tuple] = [
    # errors & stability
    ("crash",          "crash"),
    ("exception",      "exception"),
    ("memory leak",    "memory-leak"),
    ("null pointer",   "null-pointer"),
    # code health
    ("refactor",       "refactor"),
    ("cleanup",        "refactor"),
    ("deprecat",       "deprecation"),
    # reversals
    ("revert",         "revert"),
    ("rollback",       "revert"),
    # performance
    ("performance",    "performance"),
    ("slow",           "performance"),
    ("optimiz",        "performance"),
    ("optimis",        "performance"),
    ("cache",          "caching"),
    ("caching",        "caching"),
    # concurrency
    ("async",          "concurrency"),
    ("thread",         "concurrency"),
    ("race condition", "concurrency"),
    ("deadlock",       "concurrency"),
    # security
    ("security",       "security"),
    ("auth",           "auth"),
    ("permission",     "auth"),
    ("token",          "auth"),
    ("sql injection",  "security"),
    # data & storage
    ("database",       "database"),
    ("migration",      "migration"),
    ("schema",         "database"),
    ("query",          "database"),
    # api & networking
    ("api",            "api"),
    ("endpoint",       "api"),
    ("http",           "api"),
    ("request",        "api"),
    # configuration
    ("config",         "config"),
    ("settings",       "config"),
    ("environment",    "config"),
    ("env var",        "config"),
    # testing
    ("test",           "testing"),
    ("assert",         "testing"),
    ("mock",           "testing"),
    # dependencies
    ("dependency",     "dependency"),
    ("import",         "dependency"),
    ("package",        "dependency"),
    ("library",        "dependency"),
    # ui / frontend
    ("frontend",       "frontend"),
    ("ui",             "frontend"),
    ("component",      "frontend"),
    ("render",         "frontend"),
    # deployment
    ("deploy",         "deployment"),
    ("release",        "deployment"),
    ("build",          "deployment"),
    ("pipeline",       "deployment"),
    # logging & observability
    ("logging",        "logging"),
    ("log",            "logging"),
    ("monitor",        "logging"),
    ("trace",          "logging"),
    # validation
    ("validat",        "validation"),
    ("saniti",         "validation"),
]

import re as _re


# Durable-knowledge markers: text that signals a fact which holds until
# explicitly superseded (architecture, root causes, environment constraints,
# gotchas). Entries matching these get the 'durable' tag and decay as slowly
# as decisions, regardless of their recorded type.
_DURABLE_RE = _re.compile(
    r"(?:\barchitectur\w*\b|\broot caus\w*\b|\bbuild environment\b|"
    r"\bgotcha\b|\binvariant\b|\bconvention\b|"
    r"\bis not an? \w{0,20}\s?bug\b|\bnot a bug\b|"
    r"\bnever use\b|\bonly use\b|\balways use\b|\bdo not use\b|"
    r"\bmust (?:always|never)\b)",
    _re.IGNORECASE,
)


def _is_durable_knowledge(entry: "MemoryEntry") -> bool:
    text = " ".join(filter(None, [
        entry.description or "", entry.cause or "", entry.fix or "",
    ]))
    return bool(_DURABLE_RE.search(text))


# Words that look like calls in prose/code snippets but are not project functions.
_FUNC_STOPWORDS = frozenset({
    "if", "for", "while", "switch", "return", "sizeof", "catch", "assert",
    "print", "printf", "sprintf", "main", "def", "len", "str", "int", "float",
    "list", "dict", "set", "range", "type", "super", "init", "new", "delete",
    "get", "put", "run", "call", "test", "min", "max", "abs", "round", "open",
})

# A call in prose: identifier glued to "(" - "RULE (2026-10-28)" is not a call.
_FUNC_CALL_RE = _re.compile(r"(?<![A-Za-z0-9_])((?:[A-Za-z_][A-Za-z0-9_]*::)*[A-Za-z_][A-Za-z0-9_]{2,})\((\))?")

# Lower-case prose words that showed up as "functions" in real stores.
_PROSE_WORDS = frozenset((
    "text right left bars bar note notes rule rules decision decisions staff staves "
    "page pages line lines row rows column columns item items value values name "
    "names file files user client button dialog window panel label color size "
    "width height mode state step stage part parts case check fix bug issue "
    "change changes update result error see example examples and the for with").split())


def is_plausible_function(name: str, empty_parens: bool = False) -> bool:
    """Does this look like a code identifier rather than a prose word?

    Audit #4: the old extractor took any word followed by "(" - "RULE (...)",
    "DECISION (2026-10-28)", "text (left)" - and the revert detector then
    clustered unrelated entries on those fake surfaces (18 false 'unstable'
    tags in one store, top "functions": RULE, DECISION, text, right, bars).
    """
    if not name or len(name) < 3:
        return False
    base = name.rsplit("::", 1)[-1]
    low = base.lower()
    if low in _FUNC_STOPWORDS or low in _PROSE_WORDS:
        return False
    if "::" in name or "_" in base:
        return True
    if base.isupper():                      # RULE, DECISION, TODO
        return False
    if base.islower():                      # plain word: only "name()" counts
        return empty_parens
    return any(c.isupper() for c in base[1:])  # camelCase / PascalCase


def _auto_extract_functions(entry: "MemoryEntry", cap: int = 8) -> List[str]:
    """Pull function names out of fix/description text when none were supplied.

    The precise (function-level) revert detector only works where entries carry
    a ``functions`` list. Agents rarely fill the field, but their prose usually
    names the functions (``fixed pairing in attachGraceNotes()``), so harvest
    those - but only identifier-shaped ones (see is_plausible_function).
    """
    if entry.functions:
        return []
    text = " ".join(filter(None, [entry.fix or "", entry.description or ""]))
    found: List[str] = []
    for m in _FUNC_CALL_RE.finditer(text):
        name = m.group(1)
        if not is_plausible_function(name, empty_parens=bool(m.group(2))):
            continue
        if name not in found:
            found.append(name)
        if len(found) >= cap:
            break
    return found


# Standing instructions: pinned into every session, exempt from decay.
_RULE_RE = _re.compile(
    r"(?:^\s*(?:RULE|RULES|ПРАВИЛО|ПРАВИЛА|WORKING RULES?|USER RULE|CLIENT RULE)\b"
    r"|\((?:user|client) rule\))",
    _re.IGNORECASE,
)

# Ticket references. Hex colours (#1a2b3c) and standards (UTF-8) are not tasks.
_TASK_RE = _re.compile(
    r"\bTask\s*#?\s*(\d{2,6})\b|(?<![A-Za-z0-9&])#(\d{3,5})(?![0-9A-Fa-f])|\b([A-Z]{2,6}-\d{1,6})\b")
_TASK_NOT = frozenset(("UTF", "ISO", "SHA", "CP", "MD", "RFC", "HTTP", "TLS", "SSL", "WIN",
                       "VS", "MSVC", "CVE", "IEEE", "ANSI", "ASCII", "GPT", "COVID"))


def _extract_task(text: str) -> str:
    for m in _TASK_RE.finditer(text or ""):
        if m.group(1):
            return "#" + m.group(1)
        if m.group(2):
            return "#" + m.group(2)
        ref = m.group(3)
        if ref.split("-", 1)[0] not in _TASK_NOT:
            return ref
    return ""


def _auto_tag_from_description(entry: "MemoryEntry") -> List[str]:
    """Return tags to add based on keywords in description, cause, and fix.

    Only tags that are not already on the entry are returned.
    """
    text = " ".join(filter(None, [
        entry.description or "",
        entry.cause or "",
        entry.fix or "",
    ])).lower()

    existing = set(entry.tags or [])
    added: List[str] = []
    seen_tags: set = set()
    for keyword, tag in _KEYWORD_TAGS:
        if tag in seen_tags or tag in existing:
            continue
        if keyword in text:
            added.append(tag)
            seen_tags.add(tag)
    return added


class MemoryEngine:
    def __init__(self, data_dir: str | Path):
        self.storage = Storage(data_dir)
        self.updater = Updater()
        self._ensure_initialized()

    # ---------- bootstrap helpers ----------
    def _ensure_initialized(self) -> None:
        if not self.storage.exists(MEMORY_FILE):
            self.storage.write(MEMORY_FILE, [])
        if not self.storage.exists(WIKI_FILE):
            self.storage.write(WIKI_FILE, {"sections": {}, "entry_count": 0})
        if not self.storage.exists(CONFLICTS_FILE):
            self.storage.write(CONFLICTS_FILE, [])
        if not self.storage.exists(LOG_FILE):
            self.storage.write(LOG_FILE, [])

    # ---------- internal IO ----------
    def _read_memory(self) -> List[Dict[str, Any]]:
        data = self.storage.read(MEMORY_FILE, default=[])
        return data if isinstance(data, list) else []

    def _read_conflicts(self) -> List[Dict[str, Any]]:
        data = self.storage.read(CONFLICTS_FILE, default=[])
        return data if isinstance(data, list) else []

    def _log(self, action: str, affected: List[str], reason: str) -> None:
        self.storage.append_log(
            LOG_FILE,
            {
                "timestamp": _now(),
                "action": action,
                "affected": affected,
                "reason": reason,
            },
        )

    # Birth confidence by entry type when the caller does not set one.
    # A universal 0.5 birth sat one half-life away from the 0.25 floor, so any
    # unqueried entry bottomed out within two months (audit #2: 72% re-floored).
    # Decisions start high (they hold until superseded); notes are cheap.
    _BIRTH_CONFIDENCE: Dict[str, float] = {
        "decision": 0.75,
        "bug_fix":  0.60,
        "feature":  0.60,
        "note":     0.45,
        "rule":     0.90,
    }

    # Cap on conflicts recorded per single add_memory (strongest first).
    _MAX_NEW_CONFLICTS_PER_ADD: int = 3

    # Duplicate-kind conflicts at/above this similarity are merged on the spot
    # instead of quarantining both entries in status=conflict. Audit #3: nine
    # sim 0.96-0.99 "duplicate unresolved issue" conflicts held healthy entries
    # hostage for weeks — a duplicate is a hygiene problem, not a dispute.
    _AUTO_MERGE_SIM: float = 0.95

    # ---------- public API ----------
    def add_memory(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate, append, run conflict detection, refresh wiki."""
        # Extract depends_on before building MemoryEntry so we can process
        # them via DependencyGraph.add_link (which also updates required_by).
        raw_depends_on: List[str] = list(
            payload.get("depends_on", []) if isinstance(payload, dict) else []
        )
        # Strip depends_on from payload so MemoryEntry is saved without pre-set
        # links; add_link below will set both sides atomically.
        if raw_depends_on and isinstance(payload, dict):
            payload = {k: v for k, v in payload.items() if k != "depends_on"}

        # supersedes / summarizes may arrive as 8-char id prefixes (that is what
        # recall and listings show); they are resolved against the store below.
        raw_supersedes: List[str] = []
        raw_summarizes: List[str] = []
        if isinstance(payload, dict):
            raw_supersedes = [str(x) for x in (payload.get("supersedes") or []) if x]
            raw_summarizes = [str(x) for x in (payload.get("summarizes") or []) if x]
            payload = {k: v for k, v in payload.items() if k not in ("supersedes", "summarizes")}

        # Type-aware birth confidence (only when the caller didn't specify one).
        if isinstance(payload, dict) and payload.get("confidence") in (None, ""):
            payload = {
                **payload,
                "confidence": self._BIRTH_CONFIDENCE.get(
                    payload.get("type", "note"), 0.5
                ),
            }

        entry = MemoryEntry.from_dict(payload) if not isinstance(payload, MemoryEntry) else payload
        entry.validate()

        # Enrich tags automatically from description/cause/fix keywords.
        # Only adds tags not already present — never removes user-supplied tags.
        _auto_tags = _auto_tag_from_description(entry)
        if _auto_tags:
            combined = list(entry.tags)
            for t in _auto_tags:
                if t not in combined:
                    combined.append(t)
            entry.tags = combined

        # Harvest function names from the prose when the field wasn't supplied —
        # function-level surface data is what makes the revert detector precise.
        _auto_funcs = _auto_extract_functions(entry)
        if _auto_funcs:
            entry.functions = _auto_funcs

        # Durable knowledge (architecture facts, root causes, env constraints)
        # decays as slowly as a decision — see decay.entry_effective_confidence.
        if "durable" not in entry.tags and _is_durable_knowledge(entry):
            entry.tags = list(entry.tags) + ["durable"]

        # Standing instructions ("RULE: ...", "(user rule)") are pinned: always
        # injected, never decayed or archived.
        if "pinned" not in entry.tags and (
                entry.type == "rule" or _RULE_RE.search(entry.description or "")):
            entry.tags = list(entry.tags) + ["pinned"]

        # Ticket reference and git context (where/when this was learned).
        if not entry.task:
            entry.task = _extract_task(" ".join([entry.description or "", entry.cause or ""]))
        if not entry.branch:
            try:
                from .gitinfo import current as _git_current
                entry.branch, entry.commit = _git_current(self.storage.data_dir.name)
            except Exception:
                pass

        memory = self._read_memory()
        existing_entries = [MemoryEntry.from_dict(e) for e in memory]

        superseded_ids = [i for i in (self._resolve_in(memory, r) for r in raw_supersedes) if i]
        card_source_ids = [i for i in (self._resolve_in(memory, r) for r in raw_summarizes) if i]
        entry.supersedes = superseded_ids
        entry.summarizes = card_source_ids
        if card_source_ids:  # a knowledge card: durable by definition
            entry.tags = list(dict.fromkeys(list(entry.tags) + ["card", "durable"]))

        self.updater.merge_entry(memory, entry)

        # Correction chain: the entries this one replaces stop being current.
        now_iso = _now()
        for e in memory:
            if e.get("id") in superseded_ids:
                e["status"] = "superseded"
                e["superseded_by"] = entry.id
                e["valid_to"] = now_iso
            elif e.get("id") in card_source_ids:
                tags = e.get("tags") or []
                if "carded" not in tags:
                    e["tags"] = list(tags) + ["carded"]

        # Conflict detection — bounded: one add can legitimately conflict with
        # at most a few prior entries; recording every match floods the queue
        # (the same burst problem as full scans, just death by a thousand cuts).
        # NEVER run it for an entry synthesized by an auto-merge: a merged
        # entry is a near-copy of its relatives by construction, so checking it
        # re-mints duplicate conflicts and feeds the merge loop its own output
        # (a real store exploded 42 -> 978 entries before this guard existed).
        if (getattr(self, "_auto_merging", False)
                or "session-summary" in entry.tags or entry.summarizes):
            # merge output, session digests and knowledge cards are derived
            # from existing entries - comparing them back is pure noise
            conflicts = []
        else:
            existing_entries = [x for x in existing_entries if x.id not in superseded_ids]
            conflicts = find_conflicts_for(entry, existing_entries)
            conflicts.sort(key=lambda c: float(getattr(c, "similarity", 0.0)), reverse=True)
            conflicts = conflicts[: self._MAX_NEW_CONFLICTS_PER_ADD]
        conflict_dicts = self._read_conflicts()
        for c in conflicts:
            self.updater.mark_conflict(memory, c.entry_a, c.entry_b)
            conflict_dicts.append(c.to_dict())

        self.storage.write(MEMORY_FILE, memory)
        if conflicts:
            self.storage.write(CONFLICTS_FILE, conflict_dicts)

        # Rebuild the wiki but preserve operational metadata living in the same
        # file — rebuild_wiki returns a fresh dict, and dropping these keys
        # silently broke every "run at most once per day" guard (auto-decay
        # then re-applied on EVERY add, compounding decay and flooring 84% of
        # a real store's confidences).
        _META_KEYS = ("last_decay_run", "last_stabilize_run",
                      "last_conflict_triage", "unstable_detector_version",
                      "last_rendered_at", "last_hygiene_run")
        old_wiki = self.storage.read(WIKI_FILE, default={})
        wiki = self.updater.rebuild_wiki(memory)
        for k in _META_KEYS:
            if k in old_wiki and k not in wiki:
                wiki[k] = old_wiki[k]
        self.storage.write(WIKI_FILE, wiki)

        self._log(
            action="add_memory",
            affected=[entry.id] + [c.entry_b for c in conflicts],
            reason=f"new {entry.type}; {len(conflicts)} conflict(s) detected",
        )
        if superseded_ids:
            self._log("supersede", [entry.id] + superseded_ids,
                      "{} supersedes {} entr(ies)".format(entry.id, len(superseded_ids)))
        if card_source_ids:
            self._log("add_card", [entry.id] + card_source_ids,
                      "knowledge card consolidating {} entr(ies)".format(len(card_source_ids)))

        # Revert pattern detection 
        revert_warning = None
        from .revert_detector import RevertDetector
        revert_warning_obj = RevertDetector(self).check(
            entry.to_dict(),
            self._read_memory(),  # re-read: includes the just-saved entry
        )
        if revert_warning_obj is not None:
            revert_warning = revert_warning_obj.to_dict()

        # Dependency link creation
        # If caller passed depends_on IDs, create links (updates required_by on targets)
        created_links: List[str] = []
        if raw_depends_on:
            from .dependency_graph import DependencyGraph
            _dg = DependencyGraph(self)
            for target_id in raw_depends_on:
                try:
                    _dg.add_link(entry.id, target_id)
                    created_links.append(target_id)
                except (KeyError, ValueError):
                    pass  # silently skip invalid/duplicate/cycle links

        # Dependency link suggestions
        # Only for bug_fix / feature — decisions suggest themselves
        suggested_links: List[Dict[str, Any]] = []
        if entry.type in ("bug_fix", "feature"):
            from .dependency_graph import DependencyGraph
            try:
                suggested_links = DependencyGraph(self).suggest_links(
                    entry.id, threshold=0.75, top_k=3
                )
            except Exception:
                pass  # suggestions are best-effort, never block add_memory

        # File summary update
        if entry.files:
            self._update_file_summaries(entry.files)

        # Auto-decay: run once per day per project to keep confidence scores current.
        # Runs silently in the background — never blocks add_memory.
        self._maybe_auto_decay()
        self._maybe_auto_stabilize()

        # Detector upgrades: clear unstable tags left by an older algorithm.
        self._maybe_auto_recompute_unstable()

        # Auto conflict detection: run a full scan every N new entries.
        self._maybe_auto_detect_conflicts()

        # Auto-triage: dismiss conflicts that went stale without resolution.
        self._maybe_auto_triage_conflicts()

        # Daily hygiene: conservative dedup + session-summary rollup.
        self._maybe_daily_hygiene()

        # Duplicates are merged immediately, not quarantined as conflicts.
        auto_merged = self._auto_merge_duplicates([c.to_dict() for c in conflicts])

        return {
            "entry":           entry.to_dict(),
            "conflicts":       [c.to_dict() for c in conflicts],
            "revert_warning":  revert_warning,
            "created_links":   created_links,
            "suggested_links": suggested_links,
            "auto_merged":     auto_merged,
            "superseded":      superseded_ids,
        }

    # ---------- id resolution ----------

    @staticmethod
    def _resolve_in(memory: List[Dict[str, Any]], ref: str) -> Optional[str]:
        """Full id for an exact id or a unique prefix (6+ chars); else None."""
        ref = (ref or "").strip().strip("[]")
        if not ref:
            return None
        ids = [e.get("id", "") for e in memory]
        if ref in ids:
            return ref
        if len(ref) >= 6:
            hits = [i for i in ids if i.startswith(ref)]
            if len(hits) == 1:
                return hits[0]
        return None

    def resolve_id(self, ref: str) -> str:
        """Public id resolution; raises KeyError when unknown or ambiguous."""
        full = self._resolve_in(self._read_memory(), ref)
        if full is None:
            raise KeyError(f"entry not found (or ambiguous prefix): {ref}")
        return full

    def _auto_merge_duplicates(self, conflict_dicts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Resolve duplicate-kind conflicts (sim >= _AUTO_MERGE_SIM) via merge.

        The merge unions files/functions/decisions/tags, keeps the higher
        confidence, supersedes both originals and restores the knowledge to
        one active entry — instead of hiding both sides in status=conflict.
        Guarded against re-entrancy: the merge itself calls add_memory.
        """
        if getattr(self, "_auto_merging", False):
            return []
        merged: List[Dict[str, Any]] = []
        candidates = [
            c for c in conflict_dicts
            if "duplicate" in (c.get("reason") or "").lower()
            and float(c.get("similarity") or 0) >= self._AUTO_MERGE_SIM
        ]
        # Hard cap per call: pairwise merging of a large duplicate family is
        # the Deduplicator's job (single cluster merge), not a merge cascade.
        candidates = candidates[:10]
        if not candidates:
            return []
        self._auto_merging = True
        try:
            for c in candidates:
                try:
                    r = self.resolve_conflict(
                        c.get("id", ""),
                        action="merge",
                        reason="auto-merge: duplicate (similarity={:.2f})".format(
                            float(c.get("similarity") or 0)),
                    )
                    merged.append({
                        "conflict_id": c.get("id", ""),
                        "merged_entry_id": (r.get("merged_entry") or {}).get("id", ""),
                    })
                except (KeyError, ValueError):
                    pass  # already resolved or entries gone — never block add
        finally:
            self._auto_merging = False
        if merged:
            self._log(
                "auto_merge_duplicates",
                [m["merged_entry_id"] for m in merged],
                "auto-merged {} duplicate conflict(s)".format(len(merged)),
            )
        return merged

    # ---------- auto conflict detection ----------

    _CONFLICT_AUTO_TRIGGER_EVERY_N: int = 15  # run detect_conflicts every N new entries

    def _maybe_auto_detect_conflicts(self) -> None:
        """Run a full conflict scan when every N-th entry is added.

        Uses the total active-entry count as a trigger.  Stores nothing extra —
        the counter is derived from memory length so it is always consistent.
        Failures are swallowed — never blocks add_memory.
        """
        try:
            memory = self._read_memory()
            active_count = sum(
                1 for e in memory
                if e.get("status") not in {"superseded", "resolved"}
            )
            if active_count % self._CONFLICT_AUTO_TRIGGER_EVERY_N == 0:
                self.detect_conflicts()
        except Exception:
            pass  # best-effort

    # ---------- auto-decay ----------

    _DECAY_AUTO_INTERVAL_DAYS: float = 1.0  # run at most once per day per project

    def _maybe_auto_decay(self) -> None:
        """Run decay if it hasn't run in the last DECAY_AUTO_INTERVAL_DAYS days.

        Stores ``last_decay_run`` in wiki.json so the check is cheap (one read).
        Failures are swallowed — decay is best-effort and must never block add_memory.
        """
        try:
            wiki_meta = self.storage.read(WIKI_FILE, default={})
            last_decay_run: str = wiki_meta.get("last_decay_run", "")
            now = datetime.now(timezone.utc)
            if last_decay_run:
                from datetime import datetime as _dt
                last = _dt.fromisoformat(last_decay_run)
                if last.tzinfo is None:
                    from datetime import timezone as _tz
                    last = last.replace(tzinfo=_tz.utc)
                age_days = (now - last).total_seconds() / 86400.0
                if age_days < self._DECAY_AUTO_INTERVAL_DAYS:
                    return  # already ran recently

            from .decay import DecayEngine
            result = DecayEngine(self).apply(dry_run=False)

            # Update the timestamp regardless of whether any entries changed,
            # so we don't re-check on every subsequent add_memory today.
            wiki_meta["last_decay_run"] = now.isoformat()
            self.storage.write(WIKI_FILE, wiki_meta)

            if result.get("changed_count", 0) > 0:
                self._log(
                    "auto_decay",
                    [],
                    "auto-decay updated {} entry confidence(s)".format(
                        result["changed_count"]
                    ),
                )
        except Exception:
            pass  # decay is best-effort

    # ---------- detector-version auto-recompute ----------

    def _maybe_auto_recompute_unstable(self) -> None:
        """Re-evaluate all 'unstable' tags when the detector algorithm changed.

        The detector version is stored in wiki.json; when a store was last
        tagged by an older algorithm, recompute_unstable() replays detection
        with the current rules and clears tags that no longer qualify. This is
        what retroactively heals stores like gold-coop (16/16 false unstable
        under detector v1) without anyone running /memrecompute by hand.
        """
        try:
            from .revert_detector import DETECTOR_VERSION
            wiki_meta = self.storage.read(WIKI_FILE, default={})
            stored = int(wiki_meta.get("unstable_detector_version") or 0)
            if stored >= DETECTOR_VERSION:
                return

            # v3: the function extractor used to store prose words (RULE,
            # DECISION, text) as functions; clean them before replaying.
            self._clean_function_names()
            result = self.recompute_unstable(dry_run=False)

            wiki_meta = self.storage.read(WIKI_FILE, default={})
            wiki_meta["unstable_detector_version"] = DETECTOR_VERSION
            self.storage.write(WIKI_FILE, wiki_meta)

            cleared = len(result.get("cleared") or [])
            if cleared:
                self._log(
                    "auto_recompute_unstable",
                    [],
                    "detector v{} upgrade cleared {} stale unstable tag(s)".format(
                        DETECTOR_VERSION, cleared
                    ),
                )
        except Exception:
            pass  # best-effort

    # ---------- conflict auto-triage ----------

    _TRIAGE_AUTO_INTERVAL_DAYS: float = 1.0   # run at most once per day
    _TRIAGE_MAX_AGE_DAYS: float = 30.0        # conflicts older than this qualify
    _TRIAGE_CONF_CEILING: float = 0.30        # both sides must have decayed to ~floor
    # Fast tier: when both sides have clearly decayed (≤0.35), two weeks of
    # nobody caring is verdict enough — don't let the queue outlive relevance.
    _TRIAGE_FAST_AGE_DAYS: float = 14.0
    _TRIAGE_FAST_CONF_CEILING: float = 0.35

    def triage_conflicts(
        self,
        max_age_days: Optional[float] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Dismiss conflicts that went stale without anyone resolving them.

        A conflict where both entries have decayed to the confidence floor and
        which has been open for over a month is a dispute nobody cares about —
        yet it keeps both entries quarantined in status=conflict forever (a
        real store had 21% of its entries stuck this way). Dismissing restores
        both entries to active; the resolution is fully logged and reversible.
        """
        max_age = self._TRIAGE_MAX_AGE_DAYS if max_age_days is None else float(max_age_days)
        now = datetime.now(timezone.utc)
        conflicts = self._read_conflicts()
        memory_map = {e.get("id"): e for e in self._read_memory()}

        dismissed: List[Dict[str, Any]] = []
        for c in list(conflicts):
            if c.get("resolved"):
                continue
            # Age check
            try:
                ts = datetime.fromisoformat((c.get("timestamp") or "").replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_days = (now - ts).total_seconds() / 86400.0
            except (ValueError, TypeError):
                continue
            # Both sides at/near the floor?
            a = memory_map.get(c.get("entry_a"))
            b = memory_map.get(c.get("entry_b"))
            if a is None or b is None:
                continue
            conf_a = float(a.get("confidence") or 0.5)
            conf_b = float(b.get("confidence") or 0.5)
            both_max = max(conf_a, conf_b)
            # Standard tier: old + both at the floor. Fast tier: clearly
            # decayed sides qualify after two weeks instead of a month.
            standard = age_days >= max_age and both_max <= self._TRIAGE_CONF_CEILING
            fast = (age_days >= self._TRIAGE_FAST_AGE_DAYS
                    and both_max <= self._TRIAGE_FAST_CONF_CEILING)
            if not (standard or fast):
                continue

            dismissed.append({
                "conflict_id": c.get("id", ""),
                "entry_a": c.get("entry_a", ""),
                "entry_b": c.get("entry_b", ""),
                "age_days": round(age_days, 1),
            })
            if not dry_run:
                try:
                    self.resolve_conflict(
                        c.get("id", ""),
                        action="dismiss",
                        reason="auto-triage: open {:.0f}d, both sides decayed to floor".format(age_days),
                    )
                except (KeyError, ValueError):
                    dismissed.pop()

        return {
            "dismissed_count": len(dismissed),
            "dismissed": dismissed,
            "dry_run": dry_run,
            "max_age_days": max_age,
        }

    def _maybe_auto_triage_conflicts(self) -> None:
        """Daily wrapper around triage_conflicts, bookkept in wiki.json."""
        try:
            wiki_meta = self.storage.read(WIKI_FILE, default={})
            last_run: str = wiki_meta.get("last_conflict_triage", "")
            now = datetime.now(timezone.utc)
            if last_run:
                last = datetime.fromisoformat(last_run)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last).total_seconds() / 86400.0 < self._TRIAGE_AUTO_INTERVAL_DAYS:
                    return

            result = self.triage_conflicts(dry_run=False)

            wiki_meta = self.storage.read(WIKI_FILE, default={})
            wiki_meta["last_conflict_triage"] = now.isoformat()
            self.storage.write(WIKI_FILE, wiki_meta)

            if result.get("dismissed_count", 0):
                self._log(
                    "auto_triage_conflicts",
                    [d["conflict_id"] for d in result["dismissed"]],
                    "auto-dismissed {} stale conflict(s)".format(result["dismissed_count"]),
                )
        except Exception:
            pass  # best-effort

    # ---------- daily hygiene (dedup + summary rollup) ----------

    _HYGIENE_AUTO_INTERVAL_DAYS: float = 1.0
    # Unattended merges demand near-certainty: identical file sets AND
    # near-identical text (audit #2: 3 verbatim TASK-09 copies were occupying
    # half of one project's High-Confidence injection slots).
    _AUTO_DEDUP_THRESHOLD: float = 0.97

    def _maybe_daily_hygiene(self) -> None:
        """Once per day: merge verbatim duplicates and roll up old session
        summaries — the two litter sources that silently degrade injection
        quality. Best-effort, never blocks add_memory."""
        try:
            wiki_meta = self.storage.read(WIKI_FILE, default={})
            last_run: str = wiki_meta.get("last_hygiene_run", "")
            now = datetime.now(timezone.utc)
            if last_run:
                last = datetime.fromisoformat(last_run)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last).total_seconds() / 86400.0 < self._HYGIENE_AUTO_INTERVAL_DAYS:
                    return

            from .deduplicator import Deduplicator
            result = Deduplicator(self, self._AUTO_DEDUP_THRESHOLD).apply(
                dry_run=False, require_same_files=True
            )
            rolled = self._rollup_session_summaries(keep=self._SESSION_SUMMARY_KEEP)
            archived = self._archive_stale_entries()
            self._enforce_slow_floor()
            try:
                self.storage.snapshot()  # one consistent copy per day, 14 kept
            except Exception:
                pass

            wiki_meta = self.storage.read(WIKI_FILE, default={})
            wiki_meta["last_hygiene_run"] = now.isoformat()
            self.storage.write(WIKI_FILE, wiki_meta)

            merged = result.get("merged_count", 0)
            if merged or rolled or archived:
                self._log(
                    "auto_hygiene",
                    [],
                    "daily hygiene: merged {} duplicate cluster(s), rolled up {} "
                    "old session summar(ies), archived {} stale entr(ies)".format(
                        merged, rolled, archived),
                )
        except Exception:
            pass  # best-effort

    # Archive: floor-confidence entries untouched for this long leave the
    # injection and the stats (they stay searchable; a query hit revives them).
    _ARCHIVE_AFTER_DAYS: float = 30.0
    _ARCHIVE_CONF_CEILING: float = 0.26

    def _archive_stale_entries(self) -> int:
        """Tag long-unused floor-confidence entries 'archived'.

        Audit #3: 336 of 413 entries in one store sat undifferentiated at the
        0.25 floor, dragging stats down and crowding ranking. Archiving keeps
        them searchable but out of injection and health numbers. Decisions and
        durable knowledge are never archived; touch_used() lifts the tag the
        moment a query surfaces an archived entry again.
        """
        now = datetime.now(timezone.utc)
        memory = self._read_memory()
        archived_ids: List[str] = []
        for e in memory:
            if e.get("status") != "active":
                continue
            tags = e.get("tags") or []
            if "archived" in tags or "durable" in tags or "pinned" in tags:
                continue
            if e.get("type") in ("decision", "rule"):
                continue
            if int(e.get("usage_count") or 0) > 0:
                continue
            if float(e.get("confidence") or 0.5) > self._ARCHIVE_CONF_CEILING:
                continue
            anchor = e.get("last_used") or e.get("timestamp") or ""
            try:
                ts = datetime.fromisoformat(anchor)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if (now - ts).total_seconds() / 86400.0 < self._ARCHIVE_AFTER_DAYS:
                    continue
            except (ValueError, TypeError):
                continue
            e["tags"] = list(tags) + ["archived"]
            archived_ids.append(e.get("id", ""))
        if archived_ids:
            self.storage.write(MEMORY_FILE, memory)
        return len(archived_ids)

    # ---------- safety floor for decisions / durable knowledge ----------

    def _enforce_slow_floor(self) -> int:
        """Lift decisions and durable entries that sank below the slow-class
        floor (unless explicitly rejected). Decay only ever lowers stored
        confidence, so entries floored before the durable tag existed need this
        one corrective step; afterwards the decay formula holds the floor."""
        from .decay import SLOW_MIN_CONFIDENCE, is_slow, is_pinned
        memory = self._read_memory()
        lifted = 0
        for e in memory:
            if e.get("status") not in ("active", "conflict"):
                continue
            if not is_slow(e) or is_pinned(e) or "rejected" in (e.get("tags") or []):
                continue
            if float(e.get("confidence") or 0.5) < SLOW_MIN_CONFIDENCE:
                e["confidence"] = SLOW_MIN_CONFIDENCE
                e["confidence_base"] = SLOW_MIN_CONFIDENCE
                lifted += 1
        if lifted:
            self.storage.write(MEMORY_FILE, memory)
            self._log("enforce_slow_floor", [],
                      "lifted {} decision/durable entr(ies) to the {} floor".format(
                          lifted, SLOW_MIN_CONFIDENCE))
        return lifted

    def _clean_function_names(self) -> int:
        """Drop prose words that an older extractor stored as function names
        (RULE, DECISION, text, bars ...). Explicit identifiers are untouched."""
        memory = self._read_memory()
        changed = 0
        for e in memory:
            funcs = e.get("functions") or []
            keep = []
            for f in funcs:
                base = str(f).rsplit("::", 1)[-1]
                prose = (base.lower() in _PROSE_WORDS
                         or (base.isupper() and "_" not in base and "::" not in str(f)))
                if not prose:
                    keep.append(f)
            if len(keep) != len(funcs):
                e["functions"] = keep
                changed += 1
        if changed:
            self.storage.write(MEMORY_FILE, memory)
            self._log("clean_function_names", [],
                      "removed prose words from the functions of {} entr(ies)".format(changed))
        return changed

    # ---------- correction chains, pins, moves ----------

    def supersede(self, old_ref: str, new_ref: str, reason: str = "") -> Dict[str, Any]:
        """Mark ``old`` as replaced by ``new`` (both must already exist)."""
        memory = self._read_memory()
        old_id = self._resolve_in(memory, old_ref)
        new_id = self._resolve_in(memory, new_ref)
        if not old_id or not new_id or old_id == new_id:
            raise KeyError(f"cannot supersede {old_ref} with {new_ref}")
        now_iso = _now()
        for e in memory:
            if e.get("id") == old_id:
                e["status"] = "superseded"
                e["superseded_by"] = new_id
                e["valid_to"] = now_iso
            elif e.get("id") == new_id:
                sup = list(e.get("supersedes") or [])
                if old_id not in sup:
                    sup.append(old_id)
                e["supersedes"] = sup
        self.storage.write(MEMORY_FILE, memory)
        self._log("supersede", [new_id, old_id], reason or f"{new_id} supersedes {old_id}")
        return {"old": old_id, "new": new_id}

    def set_pinned(self, ref: str, pinned: bool = True) -> Dict[str, Any]:
        """Pin/unpin an entry: pinned entries are injected into every session
        and never decay or get archived."""
        memory = self._read_memory()
        eid = self._resolve_in(memory, ref)
        if not eid:
            raise KeyError(f"entry not found: {ref}")
        for e in memory:
            if e.get("id") == eid:
                tags = [t for t in (e.get("tags") or []) if t != "pinned"]
                if pinned:
                    tags.append("pinned")
                e["tags"] = tags
        self.storage.write(MEMORY_FILE, memory)
        self._log("pin" if pinned else "unpin", [eid], "pinned" if pinned else "unpinned")
        return {"id": eid, "pinned": pinned}

    def move_entries(self, refs: List[str], target_data_dir) -> Dict[str, Any]:
        """Relocate entries into another project store (ids are preserved).

        Repairs mis-filed knowledge: before stores were auto-provisioned, work
        in an unconnected folder was written to a shared 'default' store where
        three unrelated projects ended up mixed and never injected."""
        memory = self._read_memory()
        ids = {i for i in (self._resolve_in(memory, r) for r in refs) if i}
        moving = [e for e in memory if e.get("id") in ids]
        if not moving:
            return {"moved": 0, "target": str(target_data_dir)}
        target = MemoryEngine(target_data_dir)
        tmem = target._read_memory()
        have = {e.get("id") for e in tmem}
        slug = Path(target_data_dir).name
        added = 0
        for e in moving:
            if e.get("id") in have:
                continue
            e = dict(e)
            e["tags"] = [t for t in (e.get("tags") or []) if not str(t).startswith("project:")]
            e["tags"].append(f"project:{slug}")
            tmem.append(e)
            added += 1
        target.storage.write(MEMORY_FILE, tmem)
        target._log("import_entries", [e.get("id", "") for e in moving],
                    "received {} entr(ies) from store '{}'".format(added, self.storage.data_dir.name))
        self.storage.write(MEMORY_FILE, [e for e in memory if e.get("id") not in ids])
        self._log("move_entries", sorted(ids),
                  "moved {} entr(ies) to store '{}'".format(len(moving), slug))
        return {"moved": len(moving), "target": str(target_data_dir)}

    # ---------- tasks ----------

    def task_timeline(self, task: str) -> List[Dict[str, Any]]:
        """Every entry about one ticket, oldest first (superseded ones included,
        so the story of the task reads in order)."""
        key = (task or "").strip().lstrip("#").lower()
        if not key:
            return []
        out = []
        for e in self._read_memory():
            t = str(e.get("task") or "").lstrip("#").lower()
            text = " ".join([e.get("description") or "", e.get("cause") or ""]).lower()
            if t == key or _re.search(r"(?<![a-z0-9])#?" + _re.escape(key) + r"(?![a-z0-9])", text):
                out.append(e)
        out.sort(key=lambda e: e.get("timestamp") or "")
        return out

    # ---------- consolidation (knowledge cards) ----------

    def consolidation_candidates(self, min_entries: int = 8, max_clusters: int = 6) -> List[Dict[str, Any]]:
        """Clusters of entries about one file that have grown large enough to be
        distilled into a single knowledge card (written by the agent, via
        add_memory(..., summarizes=[ids])). Carded entries are not offered again."""
        skip_tags = {"archived", "card", "carded", "session-summary", "pinned"}
        pool = [e for e in self._read_memory()
                if e.get("status") in ("active", "conflict")
                and e.get("type") != "rule"
                and not (skip_tags & set(e.get("tags") or []))]
        clusters: List[Dict[str, Any]] = []
        while pool and len(clusters) < max_clusters:
            counts: Dict[str, int] = {}
            for e in pool:
                for f in set(e.get("files") or []):
                    if f:
                        counts[f] = counts.get(f, 0) + 1
            if not counts:
                break
            surface, n = max(counts.items(), key=lambda kv: kv[1])
            if n < min_entries:
                break
            members = [e for e in pool if surface in (e.get("files") or [])]
            members.sort(key=lambda e: e.get("timestamp") or "")
            clusters.append({
                "surface": surface,
                "count": len(members),
                "entries": [{
                    "id": e.get("id"), "type": e.get("type"),
                    "confidence": e.get("confidence"),
                    "timestamp": (e.get("timestamp") or "")[:10],
                    "description": e.get("description") or "",
                    "fix": (e.get("fix") or "")[:400],
                } for e in members],
            })
            gone = {e.get("id") for e in members}
            pool = [e for e in pool if e.get("id") not in gone]
        return clusters

    # ---------- automatic session digest (SessionEnd hook) ----------

    def auto_session_summary(self, min_entries: int = 2, window_hours: float = 18.0) -> Optional[Dict[str, Any]]:
        """Write a digest of what was recorded since the last session summary.

        Called by the SessionEnd hook, so the hand-off note exists without
        anyone remembering /memsession (used once in two months). The newest
        digest is shown at the next session start as "Last session"."""
        from datetime import timedelta
        memory = self._read_memory()
        now = datetime.now(timezone.utc)
        last = max((e.get("timestamp") or "" for e in memory
                    if "session-summary" in (e.get("tags") or [])), default="")
        cutoff = max(last, (now - timedelta(hours=window_hours)).isoformat())
        fresh = [e for e in memory
                 if (e.get("timestamp") or "") > cutoff
                 and e.get("status") in ("active", "conflict")
                 and "session-summary" not in (e.get("tags") or [])]
        if len(fresh) < min_entries:
            return None
        fresh.sort(key=lambda e: e.get("timestamp") or "")
        files: List[str] = []
        funcs: List[str] = []
        for e in fresh:
            for f in e.get("files") or []:
                if f not in files:
                    files.append(f)
            for fn in e.get("functions") or []:
                if fn not in funcs:
                    funcs.append(fn)
        head = "; ".join((e.get("description") or "")[:60] for e in fresh[:3])
        lines = ["- ({}) {}".format(e.get("type"), (e.get("description") or "")[:120])
                 for e in fresh[-15:]]
        result = self.add_memory({
            "type": "note",
            "description": "Session {}: {} entries - {}".format(
                now.strftime("%Y-%m-%d %H:%M"), len(fresh), head)[:300],
            "cause": "automatic end-of-session digest",
            "fix": "\n".join(lines),
            "files": files[:30],
            "functions": funcs[:30],
            "confidence": 0.6,
            "tags": ["session-summary", "auto"],
        })
        self._log("session_summary", [result["entry"]["id"]],
                  "auto digest of {} entr(ies)".format(len(fresh)))
        self._rollup_session_summaries(keep=self._SESSION_SUMMARY_KEEP)
        return result

    def warm_embeddings(self) -> int:
        """Make sure every live entry's vectors are in the disk cache.

        Conflict detection, dedup and link suggestion embed three text views of
        each entry with the similarity model; search embeds a fourth with the
        retrieval model. Uncached, the first add or query after a restart pays
        for the whole store (28 s on a 500-entry store). The daemon calls this
        in the background so no interactive operation ever does.
        """
        from . import embeddings
        from .conflict import _combined_text
        from .deduplicator import _entry_text
        from .dependency_graph import _full_text
        from .search import entry_text
        live = [e for e in self._read_memory() if e.get("status") in ("active", "conflict")]
        sim: List[str] = []
        for e in live:
            try:
                sim.append(_combined_text(MemoryEntry.from_dict(e)))
            except Exception:
                pass
            sim.append(_entry_text(e))
            try:
                sim.append(_full_text(e))
            except Exception:
                pass
        sim = list(dict.fromkeys(t for t in sim if t))
        for i in range(0, len(sim), 64):
            embeddings.embed_batch(sim[i:i + 64])
        ret = [entry_text(e) for e in live]
        for i in range(0, len(ret), 64):
            embeddings.embed_passages(ret[i:i + 64])
        return len(live)

    def run_daily_maintenance(self) -> None:
        """Run every daily-guarded maintenance hook. Called from READ paths
        (MCP query/recent) as well as writes: previously maintenance was
        piggy-backed only on add_memory, so a project consumed read-only had
        its decay, triage and hygiene frozen exactly while being used."""
        self._maybe_auto_decay()
        self._maybe_auto_stabilize()
        self._maybe_auto_recompute_unstable()
        self._maybe_auto_triage_conflicts()
        self._maybe_daily_hygiene()

    # ---------- auto-stabilize (unstable tag removal) ----------

    _STABILIZE_AUTO_INTERVAL_DAYS: float = 1.0  # run at most once per day
    _STABILIZE_DEFAULT_MIN_DAYS: int = 14        # days without revert activity → stable

    def _maybe_auto_stabilize(self) -> None:
        """Remove 'unstable' tag from entries whose files/functions have had no
        revert-related activity for at least _STABILIZE_DEFAULT_MIN_DAYS days.

        Runs at most once per day per project; piggy-backs on wiki.json for
        last-run bookkeeping. Failures are swallowed — never blocks add_memory.
        """
        try:
            wiki_meta = self.storage.read(WIKI_FILE, default={})
            last_run: str = wiki_meta.get("last_stabilize_run", "")
            now = datetime.now(timezone.utc)
            if last_run:
                from datetime import datetime as _dt
                last = _dt.fromisoformat(last_run)
                if last.tzinfo is None:
                    from datetime import timezone as _tz
                    last = last.replace(tzinfo=_tz.utc)
                if (now - last).total_seconds() / 86400.0 < self._STABILIZE_AUTO_INTERVAL_DAYS:
                    return

            result = self.stabilize_unstable_entries(
                min_stable_days=self._STABILIZE_DEFAULT_MIN_DAYS,
                dry_run=False,
            )

            wiki_meta["last_stabilize_run"] = now.isoformat()
            self.storage.write(WIKI_FILE, wiki_meta)

            if result.get("stabilized_count", 0) > 0:
                self._log(
                    "auto_stabilize",
                    result["stabilized_ids"],
                    "auto-stabilize removed 'unstable' tag from {} entries "
                    "with no revert activity for {}+ days".format(
                        result["stabilized_count"], self._STABILIZE_DEFAULT_MIN_DAYS
                    ),
                )
        except Exception:
            pass  # stabilize is best-effort

    def stabilize_unstable_entries(
        self,
        min_stable_days: int = 14,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Remove 'unstable' tag from entries that have had no revert-related
        activity on their shared files/functions for *min_stable_days* days.

        An entry is considered stable again when the most recent entry that
        touches the same files or functions AND contains revert/add keywords is
        older than *min_stable_days*.  The entry's own timestamp is included in
        that search so freshly-tagged entries are never stabilised immediately.

        Parameters
        ----------
        min_stable_days : int   Days of silence required (default 14).
        dry_run         : bool  If True, compute but do NOT write to disk.

        Returns
        -------
        dict with keys: dry_run, min_stable_days, stabilized_count, stabilized_ids,
                        skipped_too_recent (list of ids that are still within the window)
        """
        from datetime import datetime, timezone, timedelta
        from .revert_detector import _norm_set

        memory = self._read_memory()
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=min_stable_days)

        to_stabilize: List[str] = []
        still_unstable: List[str] = []

        for entry in memory:
            if "unstable" not in (entry.get("tags") or []):
                continue
            if entry.get("status") in ("superseded", "resolved"):
                continue

            e_funcs = _norm_set(entry.get("functions") or [])
            e_files = _norm_set(entry.get("files") or [])

            # Determine the most recent timestamp of any entry in the same
            # instability cluster (other unstable-tagged entries sharing files/functions).
            # Using only unstable-tagged entries avoids false positives from _ADD_RE
            # matching normal "added / fixed / implemented" language in every entry.
            last_instability: Optional[datetime] = None

            for other in memory:
                if "unstable" not in (other.get("tags") or []):
                    continue
                o_funcs = _norm_set(other.get("functions") or [])
                o_files = _norm_set(other.get("files") or [])
                if not ((e_funcs and e_funcs & o_funcs) or (e_files and e_files & o_files)):
                    continue
                ts_str = other.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        from datetime import timezone as _tz
                        ts = ts.replace(tzinfo=_tz.utc)
                    if last_instability is None or ts > last_instability:
                        last_instability = ts
                except (ValueError, TypeError):
                    pass

            if last_instability is None or last_instability < cutoff:
                to_stabilize.append(entry["id"])
            else:
                still_unstable.append(entry["id"])

        if not dry_run and to_stabilize:
            stabilized_set = set(to_stabilize)
            for entry in memory:
                if entry.get("id") in stabilized_set:
                    entry["tags"] = [t for t in (entry.get("tags") or []) if t != "unstable"]
            self.storage.write(MEMORY_FILE, memory)
            self._log(
                "stabilize_unstable",
                to_stabilize,
                "removed 'unstable' tag from {} entries "
                "(no revert activity for {}+ days)".format(len(to_stabilize), min_stable_days),
            )

        return {
            "dry_run":            dry_run,
            "min_stable_days":    min_stable_days,
            "stabilized_count":   len(to_stabilize),
            "stabilized_ids":     to_stabilize,
            "skipped_too_recent": still_unstable,
        }

    def recompute_unstable(self, dry_run: bool = False) -> Dict[str, Any]:
        """Re-evaluate every 'unstable' tag with the current RevertDetector.

        Earlier versions of the detector flagged any two entries sharing a hot
        *file* whose text happened to contain add/revert words, which tagged a
        majority of the store as unstable. This pass replays detection over the
        full history with the precision-fixed detector (function-level surface,
        description/cause classification, hotspot guard) and removes the
        'unstable' tag from entries that no longer qualify.

        Confidence is *not* restored automatically (the original pre-penalty
        value isn't recoverable); only the misleading tag is cleared.

        Returns dict: dry_run, total_unstable_before, kept, cleared (ids), reason.
        """
        from .revert_detector import RevertDetector

        memory = self._read_memory()
        detector = RevertDetector(self)

        # An entry legitimately stays unstable if replaying detection with it as
        # the "new" entry against the rest still yields a warning naming it.
        still_unstable: set = set()
        for e in memory:
            if "unstable" not in (e.get("tags") or []):
                continue
            warning = detector.check(e, memory, apply_tags=False)
            if warning is not None:
                still_unstable.add(e["id"])
                for rid in warning.related_entry_ids:
                    still_unstable.add(rid)

        cleared: List[str] = []
        before = 0
        for e in memory:
            if "unstable" not in (e.get("tags") or []):
                continue
            before += 1
            if e["id"] not in still_unstable:
                cleared.append(e["id"])
                if not dry_run:
                    e["tags"] = [t for t in e["tags"] if t != "unstable"]

        if not dry_run and cleared:
            self.storage.write(MEMORY_FILE, memory)
            self._log(
                "recompute_unstable",
                cleared,
                "cleared stale 'unstable' tag from {} entries "
                "(precision-fixed detector)".format(len(cleared)),
            )

        return {
            "dry_run":               dry_run,
            "total_unstable_before": before,
            "kept":                  before - len(cleared),
            "cleared":               cleared,
            "reason":                "re-evaluated with precision-fixed RevertDetector",
        }

    # ---------- file summaries ----------

    def _update_file_summaries(self, files: List[str]) -> None:
        """Regenerate summaries for the given file paths and persist to disk."""
        from .summarizer import FileSummarizer
        memory = self._read_memory()
        summaries: Dict[str, str] = self.storage.read(FILE_SUMMARIES_FILE, default={})
        if not isinstance(summaries, dict):
            summaries = {}
        summarizer = FileSummarizer()
        for file_path in files:
            entries_for_file = [
                e for e in memory
                if file_path in (e.get("files") or [])
            ]
            summaries[file_path] = summarizer.summarize(file_path, entries_for_file)
        self.storage.write(FILE_SUMMARIES_FILE, summaries)

    def summarize_file(self, file_path: str) -> Dict[str, Any]:
        """Return (and regenerate) the summary for a specific file.

        Parameters
        ----------
        file_path : str
            Exact file path as stored in memory entries (e.g. 'src/main.cpp').

        Returns
        -------
        dict with keys: file_path, summary, entry_count
        """
        from .summarizer import FileSummarizer
        memory = self._read_memory()
        entries_for_file = [
            e for e in memory
            if file_path in (e.get("files") or [])
        ]
        summarizer = FileSummarizer()
        summary = summarizer.summarize(file_path, entries_for_file)
        # Persist updated summary
        summaries: Dict[str, str] = self.storage.read(FILE_SUMMARIES_FILE, default={})
        if not isinstance(summaries, dict):
            summaries = {}
        summaries[file_path] = summary
        self.storage.write(FILE_SUMMARIES_FILE, summaries)
        return {
            "file_path":   file_path,
            "summary":     summary,
            "entry_count": len(entries_for_file),
        }

    def _get_file_summaries(self) -> Dict[str, str]:
        """Read current file_summaries.json (read-only helper)."""
        data = self.storage.read(FILE_SUMMARIES_FILE, default={})
        return data if isinstance(data, dict) else {}

    def list_memory(
        self,
        type_: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        memory = self._read_memory()
        out = memory
        if type_:
            out = [e for e in out if e.get("type") == type_]
        if status:
            out = [e for e in out if e.get("status") == status]
        return out

    def heal_orphaned_conflict_entries(self) -> List[str]:
        """Reset entries stuck at status='conflict' that have no matching conflict record.

        This can happen when a conflict record is manually deleted or replaced
        without updating the linked entries.  Returns the list of healed entry ids.
        """
        memory = self._read_memory()
        conflicts = self._read_conflicts()
        conflict_entry_ids: set = set()
        for c in conflicts:
            conflict_entry_ids.add(c.get("entry_a"))
            conflict_entry_ids.add(c.get("entry_b"))

        healed: List[str] = []
        for e in memory:
            if e.get("status") == "conflict" and e.get("id") not in conflict_entry_ids:
                self.updater.update_status(memory, e["id"], "active")
                healed.append(e["id"])

        if healed:
            self.storage.write(MEMORY_FILE, memory)
            self._log(
                "heal_orphaned_conflicts",
                healed,
                "reset {} orphaned conflict-status entries to active".format(len(healed)),
            )
        return healed

    # Burst guard: one full scan on a large store can mint dozens of conflicts
    # at once (audit #2: 27 in a single day), outrunning triage and drowning
    # the review queue. Keep only the strongest N new ones per scan.
    _MAX_NEW_CONFLICTS_PER_SCAN: int = 10

    def detect_conflicts(self) -> List[Dict[str, Any]]:
        """Re-scan all memory and refresh conflicts.json."""
        # Heal any entries stuck in conflict status without a matching record
        # before running the new detection pass.
        self.heal_orphaned_conflict_entries()

        memory = self._read_memory()
        entries = [MemoryEntry.from_dict(e) for e in memory]
        conflicts = find_all_conflicts(entries)

        existing = self._read_conflicts()
        existing_keys = {(c.get("entry_a"), c.get("entry_b")) for c in existing}
        fresh = [
            c for c in conflicts
            if (c.entry_a, c.entry_b) not in existing_keys
            and (c.entry_b, c.entry_a) not in existing_keys
        ]
        # Cap the burst: strongest evidence first, the rest are dropped (they
        # will resurface on a later scan if they still matter).
        fresh.sort(key=lambda c: float(getattr(c, "similarity", 0.0)), reverse=True)
        dropped = max(0, len(fresh) - self._MAX_NEW_CONFLICTS_PER_SCAN)
        fresh = fresh[: self._MAX_NEW_CONFLICTS_PER_SCAN]

        # Apply conflict status only for conflicts we actually record — marking
        # entries for dropped records would strand them as orphans.
        recorded_keys = {(c.entry_a, c.entry_b) for c in fresh} | existing_keys
        for c in conflicts:
            if (c.entry_a, c.entry_b) in recorded_keys or (c.entry_b, c.entry_a) in recorded_keys:
                self.updater.mark_conflict(memory, c.entry_a, c.entry_b)

        for c in fresh:
            existing.append(c.to_dict())

        self.storage.write(MEMORY_FILE, memory)
        self.storage.write(CONFLICTS_FILE, existing)

        self._log(
            action="detect_conflicts",
            affected=[c.entry_a for c in fresh] + [c.entry_b for c in fresh],
            reason="full scan; {} new conflict(s) recorded{}".format(
                len(fresh),
                f", {dropped} weaker one(s) dropped by burst cap" if dropped else "",
            ),
        )

        # Freshly recorded duplicates are merged, not quarantined.
        self._auto_merge_duplicates([c.to_dict() for c in fresh])

        return [c.to_dict() for c in conflicts]

    def query_memory(
        self,
        query: str,
        top_k: int = 5,
        filter_file: Optional[str] = None,
        filter_function: Optional[str] = None,
        fmt: str = "concise",
    ) -> List[Dict[str, Any]]:
        """Semantic search over memory entries.

        Args:
            query:           Natural-language query string.
            top_k:           Max results to return.
            filter_file:     If set, only return entries touching files that contain
                             this substring (e.g. 'RecordCorrection').
            filter_function: If set, only return entries where the functions list
                             contains this substring.
            fmt:             'concise' returns a lightweight summary focused on
                             decisions and description; 'full' returns the full entry.
        """
        memory = self._read_memory()

        # Apply file / function pre-filters
        if filter_file:
            memory = [
                e for e in memory
                if any(filter_file.lower() in f.lower() for f in e.get("files", []))
            ]
        if filter_function:
            memory = [
                e for e in memory
                if any(filter_function.lower() in fn.lower()
                       for fn in e.get("functions", []))
            ]

        scored: List[tuple[float, Dict[str, Any]]] = []
        from .decay import entry_effective_confidence as _entry_eff
        from .search import rank as _rank
        from datetime import datetime, timezone as _tz
        _now = datetime.now(_tz.utc)
        _sorted_ts = sorted(e.get("timestamp") or "" for e in memory)

        # A superseded entry has been replaced by a correction - returning it
        # next to (or above) its replacement is how stale facts resurface.
        memory = [e for e in memory if e.get("status") != "superseded"]

        # Hybrid relevance: BM25 over symbols/description/body + multilingual
        # embeddings (one implementation shared with the recall hook).
        ranked = _rank(memory, query)
        for e, r in zip(memory, ranked):
            # Weight: 90% relevance + 10% decayed confidence - keeps relevance
            # dominant. entry_effective_confidence applies activity-relative
            # aging and the decision/usage bonuses (shared with DecayEngine).
            conf_factor = _entry_eff(e, sorted_timestamps=_sorted_ts, now=_now)
            score = r["relevance"] * 0.9 + conf_factor * 0.1
            scored.append((score, e))

        # Recency tiebreak: entries tied on score (common at the confidence
        # floor) rank by freshest use/write instead of arbitrary list order.
        scored.sort(
            key=lambda x: (x[0], x[1].get("last_used") or x[1].get("timestamp") or ""),
            reverse=True,
        )
        top = scored[:top_k]

        file_summaries = self._get_file_summaries()

        if fmt == "concise":
            results = []
            for s, e in top:
                # Collect unique file summaries referenced by this entry
                entry_files = e.get("files", [])
                entry_file_summaries = {
                    f: file_summaries[f]
                    for f in entry_files
                    if f in file_summaries
                }
                results.append({
                    "score":          round(float(s), 4),
                    "id":             e["id"],
                    "type":           e.get("type", ""),
                    "status":         e.get("status", ""),
                    "description":    e.get("description", ""),
                    "decisions":      e.get("decisions", []),
                    "fix_summary":    (e.get("fix") or "")[:200],
                    "files":          entry_files,
                    "file_summaries": entry_file_summaries,
                    "functions":      e.get("functions", []),
                    "tags":           [t for t in e.get("tags", [])
                                       if not t.startswith("project:")],
                    "depends_on":     e.get("depends_on", []),
                    "required_by":    e.get("required_by", []),
                })
            return results

        # fmt == 'full'
        return [{"score": round(float(s), 4), "entry": e} for s, e in top]

    # ---------- conflict resolution ----------

    def list_conflicts(self) -> List[Dict[str, Any]]:
        """Return all unresolved conflicts enriched with full entry details."""
        from .conflict_resolver import ConflictResolver
        return ConflictResolver(self).list_unresolved()

    def resolve_conflict(
        self,
        conflict_id: str,
        action: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Resolve a conflict record.

        action: 'supersede_a' | 'supersede_b' | 'merge' | 'dismiss'
        """
        from .conflict_resolver import ConflictResolver
        return ConflictResolver(self).resolve(conflict_id, action, reason)

    # ---------- status / confidence ----------

    def update_status(self, entry_id: str, status: str, reason: str = "") -> Dict[str, Any]:
        memory = self._read_memory()
        updated = self.updater.update_status(memory, entry_id, status)
        self.storage.write(MEMORY_FILE, memory)
        self._log("update_status", [entry_id], reason or f"status -> {status}")
        # Test-ID warning: alert if the entry has linked tests and is being superseded
        test_warning = None
        entry_data = next((e for e in memory if e.get("id") == entry_id), {})
        test_ids = entry_data.get("test_ids") or []
        if test_ids and status == "superseded":
            test_warning = (
                f"Entry has {len(test_ids)} linked test(s). "
                f"Please verify: {test_ids}"
            )
        result = dict(updated)
        if test_warning:
            result["test_warning"] = test_warning
        return result

    def update_confidence(
        self, entry_id: str, confidence: float, reason: str = ""
    ) -> Dict[str, Any]:
        memory = self._read_memory()
        updated = self.updater.update_confidence(memory, entry_id, confidence)
        self.storage.write(MEMORY_FILE, memory)
        self._log("update_confidence", [entry_id], reason or f"confidence -> {confidence}")
        return updated

    # ---------- reinforcement (the missing feedback loop) ----------

    _REINFORCE_DELTA: float = 0.1
    _REINFORCE_CAP: float = 0.97
    _WEAKEN_DELTA: float = 0.15
    _CONF_FLOOR: float = 0.25

    def reinforce(
        self, entry_id: str, delta: Optional[float] = None, reason: str = ""
    ) -> Dict[str, Any]:
        """Confirm a memory: bump confidence, count the use, reset the decay clock.

        This is the feedback loop the store previously lacked — confidence could
        only ever fall (decay / unstable penalty). Call when an entry proved
        correct or was reused successfully.
        """
        from datetime import datetime, timezone as _tz
        d = self._REINFORCE_DELTA if delta is None else float(delta)
        memory = self._read_memory()
        entry_id = self._resolve_in(memory, entry_id) or entry_id
        now_iso = datetime.now(_tz.utc).isoformat()
        found = None
        for e in memory:
            if e.get("id") == entry_id:
                old = float(e.get("confidence") or 0.5)
                e["confidence"] = round(min(self._REINFORCE_CAP, old + d), 4)
                if "rejected" in (e.get("tags") or []):
                    e["tags"] = [t for t in e["tags"] if t != "rejected"]
                # A deliberate confidence change sets a new decay baseline.
                e["confidence_base"] = e["confidence"]
                e["usage_count"] = int(e.get("usage_count") or 0) + 1
                e["last_used"] = now_iso
                found = {"id": entry_id, "old": old, "new": e["confidence"],
                         "usage_count": e["usage_count"]}
                break
        if found is None:
            raise KeyError(f"entry not found: {entry_id}")
        self.storage.write(MEMORY_FILE, memory)
        self._log("reinforce", [entry_id],
                  reason or f"reinforced {found['old']} -> {found['new']}")
        return found

    def weaken(
        self, entry_id: str, delta: Optional[float] = None, reason: str = ""
    ) -> Dict[str, Any]:
        """Reject a memory: lower confidence toward the floor (it misled you)."""
        d = self._WEAKEN_DELTA if delta is None else float(delta)
        memory = self._read_memory()
        entry_id = self._resolve_in(memory, entry_id) or entry_id
        found = None
        for e in memory:
            if e.get("id") == entry_id:
                old = float(e.get("confidence") or 0.5)
                e["confidence"] = round(max(self._CONF_FLOOR, old - d), 4)
                # An explicit rejection lifts the durable/decision safety floor.
                if "rejected" not in (e.get("tags") or []):
                    e["tags"] = list(e.get("tags") or []) + ["rejected"]
                # A deliberate confidence change sets a new decay baseline.
                e["confidence_base"] = e["confidence"]
                found = {"id": entry_id, "old": old, "new": e["confidence"]}
                break
        if found is None:
            raise KeyError(f"entry not found: {entry_id}")
        self.storage.write(MEMORY_FILE, memory)
        self._log("weaken", [entry_id],
                  reason or f"weakened {found['old']} -> {found['new']}")
        return found

    def touch_used(self, entry_ids: List[str]) -> int:
        """Mark entries as just-recalled: reset decay clock without changing
        confidence. Called when query_memory surfaces an entry to the agent."""
        if not entry_ids:
            return 0
        from datetime import datetime, timezone as _tz
        ids = set(entry_ids)
        now_iso = datetime.now(_tz.utc).isoformat()
        memory = self._read_memory()
        n = 0
        for e in memory:
            if e.get("id") in ids:
                e["last_used"] = now_iso
                # Revival: a recalled entry is relevant again — un-archive it.
                tags = e.get("tags") or []
                if "archived" in tags:
                    e["tags"] = [t for t in tags if t != "archived"]
                n += 1
        if n:
            self.storage.write(MEMORY_FILE, memory)
        return n

    # ---------- dependency graph ----------

    def add_dependency_link(self, from_id: str, to_id: str) -> Dict[str, Any]:
        """Create a directed link: from_id depends_on to_id.

        Updates both entries atomically:
            from_entry.depends_on += [to_id]
            to_entry.required_by  += [from_id]
        Raises KeyError if either id not found.
        Raises ValueError if link exists, would create a cycle, or is self-referential.
        """
        from .dependency_graph import DependencyGraph
        return DependencyGraph(self).add_link(from_id, to_id)

    def remove_dependency_link(self, from_id: str, to_id: str) -> Dict[str, Any]:
        """Remove a directed link: from_id no longer depends_on to_id."""
        from .dependency_graph import DependencyGraph
        return DependencyGraph(self).remove_link(from_id, to_id)

    def get_dependencies(
        self,
        entry_id: str,
        depth: int = 1,
    ) -> Dict[str, Any]:
        """Return dependency subgraph for entry_id.

        depth=1   direct links only (default)
        depth=-1  full transitive closure
        depth=N   N levels deep
        """
        from .dependency_graph import DependencyGraph
        return DependencyGraph(self).get_dependencies(entry_id, depth=depth)

    def suggest_links(
        self,
        entry_id: str,
        threshold: float = 0.75,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Suggest potential depends_on links based on semantic similarity.

        Returns ranked list of decision entries that are semantically
        similar to entry_id. Results are suggestions only — never auto-committed.
        """
        from .dependency_graph import DependencyGraph
        return DependencyGraph(self).suggest_links(entry_id, threshold, top_k)

    # ---------- deduplication ----------

    def find_duplicate_clusters(
        self, threshold: float = 0.88
    ) -> List[Dict[str, Any]]:
        """Return clusters of near-duplicate entries (read-only preview).

        Each cluster dict contains:
            canonical_id, cluster_size, member_ids, descriptions, similarities
        """
        from .deduplicator import Deduplicator
        return Deduplicator(self, threshold).find_clusters()

    def deduplicate(
        self,
        dry_run: bool = False,
        threshold: float = 0.88,
    ) -> Dict[str, Any]:
        """Find and merge all near-duplicate entry clusters.

        Parameters
        ----------
        dry_run   : if True, compute clusters but do NOT write to disk.
        threshold : cosine similarity threshold (default 0.88).

        Returns a summary dict with cluster details.
        """
        from .deduplicator import Deduplicator
        return Deduplicator(self, threshold).apply(dry_run=dry_run)

    # ---------- confidence decay ----------

    def decay(
        self,
        dry_run: bool = False,
        half_life_days: float = 60.0,
        min_confidence: float = 0.40,
    ) -> Dict[str, Any]:
        """Apply time-based confidence decay to all active entries.

        Parameters
        ----------
        dry_run        : if True, compute changes but do NOT write to disk.
        half_life_days : days until confidence halves (default 60).
        min_confidence : absolute floor, never goes below this (default 0.40).

        Returns a dict describing what changed (or would change).
        """
        from .decay import DecayEngine
        return DecayEngine(self, half_life_days, min_confidence).apply(dry_run=dry_run)

    def decay_preview(self) -> List[Dict[str, Any]]:
        """Return all entries with effective_confidence field (read-only)."""
        from .decay import DecayEngine
        return DecayEngine(self).preview()

    def state(self) -> Dict[str, Any]:
        memory = self._read_memory()
        wiki = self.storage.read(WIKI_FILE, default={})
        conflicts = self._read_conflicts()
        return {
            "embedding_backend": embed_backend(),
            "entry_count": len(memory),
            "conflict_count": len(conflicts),
            "wiki": wiki,
        }

    # ---------- stale entry detection ----------

    def check_stale(
        self,
        repo_path: str,
        min_age_days: int = 7,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Detect memory entries whose referenced files/functions no longer exist.

        For each active entry older than *min_age_days*:
        1. Check if all ``entry.files`` still exist in the repo working tree.
        2. Check if all ``entry.functions`` still appear in those files.
        3. If anything is missing: tag the entry with ``"stale"`` (unless dry_run).

        Parameters
        ----------
        repo_path    : str   Absolute path to the git repository root.
        min_age_days : int   Only check entries older than this (default 7).
        dry_run      : bool  If True, compute results but do NOT write to disk.

        Returns
        -------
        dict with keys:
            repo_path       : str
            dry_run         : bool
            checked         : int     — number of entries inspected
            stale_count     : int     — entries flagged as stale
            candidates      : list    — full detail per stale entry
            is_git_repo     : bool    — whether git was available
        """
        from .git_inspector import GitInspector
        from datetime import datetime, timezone, timedelta

        inspector = GitInspector(repo_path)
        memory = self._read_memory()
        cutoff = datetime.now(timezone.utc) - timedelta(days=min_age_days)

        candidates: List[Dict[str, Any]] = []
        checked = 0

        for entry in memory:
            if entry.get("status") != "active":
                continue
            # Skip entries with no files and no functions (nothing to check)
            if not entry.get("files") and not entry.get("functions"):
                continue
            # Age filter
            ts_str = entry.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                ts = datetime.min.replace(tzinfo=timezone.utc)
            if ts > cutoff:
                continue  # too recent to check

            checked += 1
            result = inspector.check_entry(entry)
            if result["is_stale"]:
                candidates.append({
                    "entry_id":      result["entry_id"],
                    "description":   (entry.get("description") or "")[:120],
                    "missing_files": result["missing_files"],
                    "missing_fns":   result["missing_fns"],
                    "reason":        result["reason"],
                    "timestamp":     entry.get("timestamp", ""),
                    "confidence":    entry.get("confidence", 0),
                })

        if not dry_run and candidates:
            stale_ids = {c["entry_id"] for c in candidates}
            for entry in memory:
                if entry.get("id") in stale_ids:
                    tags = list(entry.get("tags") or [])
                    if "stale" not in tags:
                        tags.append("stale")
                    entry["tags"] = tags
            self.storage.write(MEMORY_FILE, memory)
            self._log(
                "check_stale",
                [c["entry_id"] for c in candidates],
                f"tagged {len(candidates)} stale entry/entries (repo: {repo_path})",
            )

        return {
            "repo_path":   repo_path,
            "dry_run":     dry_run,
            "checked":     checked,
            "stale_count": len(candidates),
            "candidates":  candidates,
            "is_git_repo": inspector.is_git_repo(),
        }

    # ---------- markdown projection ----------
    def render_wiki_md(self) -> Dict[str, Any]:
        """Regenerate the markdown wiki under data/wiki/.

        Skips rendering if no entries have been added or modified since the
        last render.  This makes it safe to call after every ``add_memory``
        without wasting I/O on unchanged data.
        """
        # --- dirty check ---
        wiki_meta = self.storage.read(WIKI_FILE, default={})
        last_rendered_at: str = wiki_meta.get("last_rendered_at", "")

        if last_rendered_at:
            memory = self._read_memory()
            latest_ts = max(
                (e.get("timestamp", "") for e in memory),
                default="",
            )
            if latest_ts and latest_ts <= last_rendered_at:
                return {
                    "skipped": True,
                    "reason": "wiki already up to date",
                    "last_rendered_at": last_rendered_at,
                }

        renderer = WikiRenderer(self.storage.data_dir)
        report = renderer.render_all()

        # Persist the render timestamp so future calls can skip if unchanged
        wiki_meta["last_rendered_at"] = _now()
        self.storage.write(WIKI_FILE, wiki_meta)

        self._log(
            action="render_wiki_md",
            affected=[],
            reason=(
                f"rendered {report['entries_pages']} entry page(s), "
                f"{report['file_pages']} file page(s)"
            ),
        )
        return report


    # ---------- update copilot-instructions.md ----------

    def update_instructions(
        self,
        project_path: str,
        min_confidence: float = 0.80,
        dry_run: bool = False,
    ) -> dict:
        """Generate a Learned Patterns section from high-confidence decisions
        and write it into the project's .github/copilot-instructions.md.

        Only entries with:
          - status == "active"
          - confidence >= min_confidence
          - at least one non-empty decision string
        are included.

        Args:
            project_path:   Root of the project (must contain .github/).
            min_confidence: Minimum confidence score to include (default 0.80).
            dry_run:        If True, return the generated content without writing.

        Returns a dict with keys: patterns_count, content, updated (bool).
        """
        from pathlib import Path as _Path
        from .vscode_infra import VSCodeInfraBuilder

        memory = self._read_memory()

        # Collect decisions from qualifying entries
        # Use decayed confidence so stale decisions don't pollute Learned Patterns
        from .decay import effective_confidence as _eff_conf
        from datetime import datetime, timezone as _tz
        _now = datetime.now(_tz.utc)

        patterns: list[str] = []
        for e in memory:
            if e.get("status") not in ("active",):
                continue
            eff_conf = _eff_conf(
                original=float(e.get("confidence") or 0.0),
                timestamp=e.get("timestamp") or "",
                now=_now,
            )
            if eff_conf < min_confidence:
                continue
            decisions = e.get("decisions") or []
            for d in decisions:
                d = d.strip()
                if d and d not in patterns:
                    patterns.append(d)

        if not patterns:
            return {
                "patterns_count": 0,
                "content": "",
                "updated": False,
                "reason": "no qualifying decisions found (min_confidence={})".format(min_confidence),
            }

        # Build section content
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [
            "<!-- Auto-generated from AI Memory System decisions on {} -->".format(now),
            "",
        ]
        for p in patterns:
            # Normalise to a single-line bullet
            single = " ".join(p.split())
            lines.append("- {}".format(single))
        lines.append("")
        section_content = "\n".join(lines)

        if dry_run:
            return {
                "patterns_count": len(patterns),
                "content": section_content,
                "updated": False,
                "dry_run": True,
            }

        builder = VSCodeInfraBuilder(self.storage.data_dir)
        updated = builder.update_instructions(
            project_path, "Learned Patterns", section_content
        )

        self._log(
            "update_instructions",
            [],
            "wrote {} pattern(s) to {}" .format(len(patterns), project_path),
        )

        return {
            "patterns_count": len(patterns),
            "content": section_content,
            "updated": updated,
            "project_path": project_path,
        }

    # ---------- session summary ----------
    def session_summary(
        self,
        description: str,
        tags: Optional[List[str]] = None,
        since_n: int = 20,
    ) -> Dict[str, Any]:
        """Create a summary entry that captures the most recent session work.

        Reads the last *since_n* entries, collects all unique files/functions
        touched, and creates a single "note" type entry that summarises the
        session.  This is the recommended end-of-session command.

        Args:
            description: One-line summary of what the session accomplished.
            tags:        Optional extra tags.
            since_n:     How many recent entries to aggregate over.

        Returns a dict with the created entry and any conflicts detected.
        """
        memory = self._read_memory()
        recent = sorted(
            memory,
            key=lambda e: e.get("timestamp", ""),
            reverse=True,
        )[:since_n]

        # Aggregate files & functions touched in the session
        agg_files: list[str] = []
        agg_functions: list[str] = []
        for e in recent:
            for f in e.get("files", []):
                if f not in agg_files:
                    agg_files.append(f)
            for fn in e.get("functions", []):
                if fn not in agg_functions:
                    agg_functions.append(fn)

        # Build an auto-cause from descriptions of recent entries
        causes = [e.get("description", "")[:80] for e in recent[:5] if e.get("description")]
        auto_cause = "; ".join(causes) if causes else "end-of-session aggregation"

        payload: Dict[str, Any] = {
            "type": "note",
            "description": description,
            "cause": auto_cause,
            "fix": f"Session covered {len(recent)} entr(ies) and {len(agg_files)} file(s).",
            "files": agg_files[:30],
            "functions": agg_functions[:30],
            "decisions": [],
            "confidence": 0.9,
            "tags": (tags or []) + ["session-summary"],
        }
        result = self.add_memory(payload)
        self._log(
            "session_summary",
            [result["entry"]["id"]],
            f"aggregated {len(recent)} recent entries over {len(agg_files)} files",
        )
        self._rollup_session_summaries(keep=5)
        return result

    _SESSION_SUMMARY_KEEP: int = 5

    def _rollup_session_summaries(self, keep: int = 5) -> int:
        """Supersede session-summary notes beyond the newest ``keep``.

        Session summaries are snapshots, not knowledge: 29 of them piled up in
        one real project, all decayed to the floor, polluting type=note stats
        and query results. Only the recent ones carry context worth recalling.
        """
        try:
            memory = self._read_memory()
            summaries = [
                e for e in memory
                if "session-summary" in (e.get("tags") or [])
                and e.get("status") == "active"
            ]
            summaries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
            stale = summaries[keep:]
            for e in stale:
                self.update_status(
                    e["id"], "superseded",
                    reason=f"session-summary rollup: keeping newest {keep}",
                )
            return len(stale)
        except Exception:
            return 0  # rollup is best-effort, never blocks session_summary
