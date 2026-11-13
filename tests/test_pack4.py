"""Tests for improvement pack 4 (audit #4):

storage   - SQLite backend behind the Storage API, legacy import, snapshots
fixes     - function extractor, rules/pins, slow floor, id prefixes
knowledge - supersedes, tasks, cards, session digest, moves
recall    - BM25 ranking, per-prompt selection, session dedupe, storm guard
inject    - section budget, rules first, compact mode
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.context_injector import MAX_CHARS, _build_summary
from core.decay import SLOW_MIN_CONFIDENCE, DecayEngine, entry_effective_confidence
from core.engine import MemoryEngine, _auto_extract_functions, _extract_task, is_plausible_function
from core.models import MemoryEntry
from core.recall import recall_for_prompt, select
from core.search import BM25, entry_tokens, rank, tokenize
from core.storage import Storage, has_store, read_store, store_entry_count


def _ts(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ── storage ──────────────────────────────────────────────────────────────────

class TestSqliteStorage:
    def test_roundtrip_and_order(self, tmp_path):
        st = Storage(tmp_path)
        items = [{"id": f"e{i}", "v": i} for i in range(5)]
        st.write("memory.json", items)
        assert st.read("memory.json", None) == items
        assert (tmp_path / "memory.db").exists()
        assert not (tmp_path / "memory.json").exists()

    def test_diff_write_updates_inserts_deletes(self, tmp_path):
        st = Storage(tmp_path)
        st.write("memory.json", [{"id": "a", "v": 1}, {"id": "b", "v": 1}, {"id": "c", "v": 1}])
        st.write("memory.json", [{"id": "a", "v": 2}, {"id": "c", "v": 1}, {"id": "d", "v": 9}])
        assert st.read("memory.json", []) == [{"id": "a", "v": 2}, {"id": "c", "v": 1}, {"id": "d", "v": 9}]

    def test_reorder_falls_back_to_full_rewrite(self, tmp_path):
        st = Storage(tmp_path)
        st.write("memory.json", [{"id": "a"}, {"id": "b"}, {"id": "c"}])
        st.write("memory.json", [{"id": "c"}, {"id": "a"}, {"id": "b"}])
        assert [e["id"] for e in st.read("memory.json", [])] == ["c", "a", "b"]

    def test_append_log_and_event_queries(self, tmp_path):
        st = Storage(tmp_path)
        for i in range(3):
            st.append_log("activity_log.json", {"timestamp": _ts(0), "action": "inject_context", "n": i})
        st.append_log("activity_log.json", {"timestamp": _ts(2), "action": "inject_context"})
        assert len(st.read("activity_log.json", [])) == 4
        assert st.count_events("inject_context", _ts(1)) == 3

    def test_default_until_written_and_exists(self, tmp_path):
        st = Storage(tmp_path)
        assert st.read("wiki.json", {"x": 1}) == {"x": 1}
        assert not st.exists("memory.json")
        st.write("memory.json", [])
        assert st.exists("memory.json")
        assert st.read("memory.json", None) == []

    def test_legacy_json_is_imported_once(self, tmp_path):
        legacy = [{"id": "old1", "description": "from json"}]
        (tmp_path / "memory.json").write_text(json.dumps(legacy), encoding="utf-8")
        (tmp_path / "activity_log.json").write_text(
            json.dumps([{"timestamp": _ts(1), "action": "add_memory"}]), encoding="utf-8")
        st = Storage(tmp_path)
        assert st.read("memory.json", []) == legacy
        assert len(st.read("activity_log.json", [])) == 1
        assert not (tmp_path / "memory.json").exists()
        assert (tmp_path / "legacy_json" / "memory.json").exists()

    def test_late_writer_json_is_merged_not_replaced(self, tmp_path):
        st = Storage(tmp_path)
        st.write("memory.json", [{"id": "a"}, {"id": "b"}])
        # a process still running pre-migration code recreates memory.json
        (tmp_path / "memory.json").write_text(json.dumps([{"id": "a"}, {"id": "late"}]), encoding="utf-8")
        st2 = Storage(tmp_path)
        assert sorted(e["id"] for e in st2.read("memory.json", [])) == ["a", "b", "late"]
        assert not (tmp_path / "memory.json").exists()

    def test_snapshot_and_bulk_delete_guard(self, tmp_path):
        st = Storage(tmp_path)
        st.write("memory.json", [{"id": f"e{i}"} for i in range(40)])
        snap = st.snapshot()
        assert snap.exists()
        st.write("memory.json", [{"id": "e0"}])  # deletes 39 of 40 -> automatic safety snapshot
        guards = list((tmp_path / ".snapshots").glob("memory-*-pre-bulk-delete-*.db"))
        assert len(guards) == 1

    def test_readonly_helpers_do_not_create_stores(self, tmp_path):
        empty = tmp_path / "nothing_here"
        empty.mkdir()
        assert not has_store(empty)
        assert read_store(empty, "memory.json", []) == []
        assert store_entry_count(empty) == 0
        assert not (empty / "memory.db").exists()

    def test_session_seen_set(self, tmp_path):
        st = Storage(tmp_path)
        st.mark_seen("s1", ["a", "b"])
        assert st.seen_in_session("s1") == {"a", "b"}
        assert st.seen_in_session("s2") == set()


# ── function extractor ───────────────────────────────────────────────────────

class TestFunctionExtractor:
    @pytest.mark.parametrize("name,empty,ok", [
        ("attachGraceNotes", False, True),
        ("OnBarBoundary", False, True),
        ("chirp_start", False, True),
        ("CScoreView::IsSmcApp", False, True),
        ("malloc", True, True),
        ("malloc", False, False),
        ("RULE", False, False),
        ("DECISION", True, False),
        ("text", True, False),
        ("bars", False, False),
        ("MAX_LEN", False, True),
    ])
    def test_plausibility(self, name, empty, ok):
        assert is_plausible_function(name, empty) is ok

    def test_prose_parentheses_are_not_calls(self):
        e = MemoryEntry(description="RULE (2026-10-28): the right column docks dialogs",
                        fix="DECISION (client): text (left) aligned; see attachGraceNotes() and quantizeBar(bar)")
        assert _auto_extract_functions(e) == ["attachGraceNotes", "quantizeBar"]

    def test_clean_function_names_removes_prose(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        r = eng.add_memory({"type": "note", "description": "polluted entry about layout",
                            "functions": ["RULE", "text", "SetTempo", "DECISION"]})
        eng._clean_function_names()   # (the detector upgrade may already have run it)
        e = next(x for x in eng._read_memory() if x["id"] == r["entry"]["id"])
        assert e["functions"] == ["SetTempo"]


# ── rules, floors, ids ───────────────────────────────────────────────────────

class TestRulesAndFloors:
    def test_rule_is_pinned_and_born_high(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        r = eng.add_memory({"type": "rule", "description": "Never touch the toolbar layout without asking"})
        assert r["entry"]["confidence"] == pytest.approx(0.9)
        assert "pinned" in r["entry"]["tags"]

    def test_rule_marker_pins_any_type(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        r = eng.add_memory({"type": "decision", "description": "WORKING RULES for the redesign stream: ask first"})
        assert "pinned" in r["entry"]["tags"]
        r2 = eng.add_memory({"type": "decision", "description": "Layout model (user rule): toolbar stays on top"})
        assert "pinned" in r2["entry"]["tags"]

    def test_pinned_entry_never_decays(self):
        now = datetime.now(timezone.utc)
        old = _ts(300)
        busy = sorted([old] + [_ts(1)] * 150)
        rule = {"confidence": 0.9, "timestamp": old, "type": "rule"}
        assert entry_effective_confidence(rule, sorted_timestamps=busy, now=now) == pytest.approx(0.9)

    def test_pinned_skipped_by_decay_and_archive(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        rid = eng.add_memory({"type": "rule", "description": "Always build with the vcpkg toolchain"})["entry"]["id"]
        mem = eng._read_memory()
        for e in mem:
            e["timestamp"] = _ts(90)
            e["confidence"] = 0.25
        eng.storage.write("memory.json", mem)
        for i in range(5):
            eng.add_memory({"type": "note", "description": f"later unrelated work item number {i}"})
        DecayEngine(eng).apply(dry_run=False)
        assert eng._archive_stale_entries() == 0
        e = next(x for x in eng._read_memory() if x["id"] == rid)
        assert e["confidence"] == pytest.approx(0.25) and "archived" not in e["tags"]

    def test_slow_floor_holds_and_lifts(self, tmp_path):
        now = datetime.now(timezone.utc)
        old = _ts(400)
        busy = sorted([old] + [_ts(1)] * 200)
        dur = {"confidence": 0.9, "timestamp": old, "type": "note", "tags": ["durable"]}
        assert entry_effective_confidence(dur, sorted_timestamps=busy, now=now) >= SLOW_MIN_CONFIDENCE
        rejected = dict(dur, tags=["durable", "rejected"])
        assert entry_effective_confidence(rejected, sorted_timestamps=busy, now=now) < SLOW_MIN_CONFIDENCE

        eng = MemoryEngine(str(tmp_path))
        a = eng.add_memory({"type": "note", "description": "ARCHITECTURE: ids share one number space",
                            "confidence": 0.25})["entry"]["id"]
        b = eng.add_memory({"type": "decision", "description": "use approach B for staves",
                            "confidence": 0.25})["entry"]["id"]
        eng.weaken(b)  # explicit rejection keeps it low
        eng._enforce_slow_floor()     # (daily hygiene may already have lifted `a`)
        mem = {e["id"]: e for e in eng._read_memory()}
        assert mem[a]["confidence"] == pytest.approx(SLOW_MIN_CONFIDENCE)
        assert mem[b]["confidence"] < SLOW_MIN_CONFIDENCE and "rejected" in mem[b]["tags"]

    def test_reinforce_accepts_id_prefix_and_clears_rejected(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        eid = eng.add_memory({"type": "note", "description": "something worth confirming later"})["entry"]["id"]
        eng.weaken(eid[:8])
        r = eng.reinforce(eid[:8])
        assert r["id"] == eid and r["usage_count"] == 1
        e = next(x for x in eng._read_memory() if x["id"] == eid)
        assert "rejected" not in e["tags"]


# ── knowledge layer ──────────────────────────────────────────────────────────

class TestSupersedes:
    def test_add_with_supersedes_prefix(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        old = eng.add_memory({"type": "note", "description": "IsSmcApp is reliable for scan mode"})["entry"]["id"]
        r = eng.add_memory({"type": "note", "description": "CORRECTION: IsSmcApp is NOT reliable for scan mode",
                            "supersedes": [old[:8]]})
        assert r["superseded"] == [old] and r["entry"]["supersedes"] == [old]
        assert r["conflicts"] == []          # a correction is not a conflict with what it corrects
        mem = {e["id"]: e for e in eng._read_memory()}
        assert mem[old]["status"] == "superseded"
        assert mem[old]["superseded_by"] == r["entry"]["id"] and mem[old]["valid_to"]

    def test_supersede_existing_pair(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        a = eng.add_memory({"type": "note", "description": "first belief about the dialog"})["entry"]["id"]
        b = eng.add_memory({"type": "note", "description": "unrelated newer truth about parsing"})["entry"]["id"]
        eng.supersede(a, b)
        mem = {e["id"]: e for e in eng._read_memory()}
        assert mem[a]["status"] == "superseded" and mem[b]["supersedes"] == [a]


class TestTasks:
    @pytest.mark.parametrize("text,task", [
        ("Task #1145 root cause found", "#1145"),
        ("fixed in #1146 after review", "#1146"),
        ("SS-809 CORRECTION: guard is wrong", "SS-809"),
        ("file is saved as UTF-8 text", ""),
        ("background colour #1a2b3c applied", ""),
        ("accent #404040 everywhere", ""),
        ("no ticket mentioned here", ""),
    ])
    def test_extract(self, text, task):
        assert _extract_task(text) == task

    def test_timeline_in_order(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        eng.add_memory({"type": "note", "description": "Task #1145 investigation: harpsichord has two staves"})
        eng.add_memory({"type": "note", "description": "unrelated toolbar colour tweak"})
        eng.add_memory({"type": "bug_fix", "description": "instrument id never set", "task": "#1145",
                        "cause": "dialog skipped the setter", "fix": "set it in OnAccept()"})
        tl = eng.task_timeline("1145")
        assert [e["type"] for e in tl] == ["note", "bug_fix"]
        assert all(e["task"] == "#1145" for e in tl)


class TestCardsAndDigest:
    def _seed(self, eng, n, file="Source/SMusicWindow.cpp"):
        topics = ["dock dragging", "header bar buttons", "splitter persistence", "theme reload",
                  "status bar text", "menu shortcuts", "zoom slider", "recent files list",
                  "window geometry restore", "toolbar overflow"]
        return [eng.add_memory({"type": "feature", "description": f"main window: {topics[i]} reworked",
                                "fix": f"changed handling of {topics[i]}", "files": [file],
                                "confidence": 0.6})["entry"]["id"] for i in range(n)]

    def test_candidates_and_card(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        ids = self._seed(eng, 9)
        clusters = eng.consolidation_candidates(min_entries=8)
        assert len(clusters) == 1 and clusters[0]["count"] == 9
        card = eng.add_memory({"type": "note", "description": "CARD SMusicWindow.cpp: current main window design",
                               "fix": "dock is fixed; header owns buttons; geometry restored on start",
                               "files": ["Source/SMusicWindow.cpp"], "summarizes": [i[:8] for i in ids]})
        assert {"card", "durable"} <= set(card["entry"]["tags"])
        assert card["conflicts"] == []
        mem = {e["id"]: e for e in eng._read_memory()}
        assert all("carded" in mem[i]["tags"] and mem[i]["status"] == "active" for i in ids)
        assert eng.consolidation_candidates(min_entries=8) == []   # not offered twice
        summary = _build_summary(eng._read_memory(), [], "p")
        assert "CARD SMusicWindow.cpp" in summary
        assert "dock dragging reworked" not in summary             # covered by the card

    def test_auto_session_summary(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        eng.add_memory({"type": "note", "description": "only one thing happened this session"})
        assert eng.auto_session_summary() is None
        eng.add_memory({"type": "bug_fix", "description": "second thing: crash in loader fixed",
                        "cause": "null deref", "fix": "guard added"})
        r = eng.auto_session_summary()
        assert r is not None and "session-summary" in r["entry"]["tags"]
        assert eng.auto_session_summary() is None                  # nothing new since the digest
        summary = _build_summary(eng._read_memory(), [], "p")
        assert "### Last session" in summary and "crash in loader fixed" in summary

    def test_move_entries(self, tmp_path):
        src = MemoryEngine(str(tmp_path / "default"))
        a = src.add_memory({"type": "decision", "description": "course rule about self-contained tests",
                            "tags": ["project:default"]})["entry"]["id"]
        keep = src.add_memory({"type": "note", "description": "stays where it is"})["entry"]["id"]
        target_dir = tmp_path / "cpp_course"
        r = src.move_entries([a[:8]], target_dir)
        assert r["moved"] == 1
        assert [e["id"] for e in src._read_memory()] == [keep]
        moved = MemoryEngine(str(target_dir))._read_memory()
        assert [e["id"] for e in moved] == [a] and "project:cpp_course" in moved[0]["tags"]


# ── retrieval ────────────────────────────────────────────────────────────────

class TestSearch:
    def test_tokenize_splits_identifiers(self):
        toks = tokenize("fix attachGraceNotes in score_note_inserter")
        assert {"attachgracenotes", "attach", "grace", "notes", "score", "inserter"} <= set(toks)
        assert tokenize("так, продовжуй") == ["продовжуй"]

    def test_bm25_prefers_focused_over_umbrella(self):
        focused = {"id": "f", "description": "dock panel cannot be dragged out",
                   "fix": "disabled floating for the dock", "files": ["Dock.cpp"]}
        umbrella = {"id": "u", "description": "redesign stage one covering everything",
                    "fix": " ".join(["toolbar header splitter theme menu status geometry zoom dock panel"] * 25),
                    "files": ["A.cpp", "B.cpp", "C.cpp"]}
        bm = BM25([entry_tokens(focused), entry_tokens(umbrella)] + [["filler", "words"]] * 6)
        scores, _ = bm.score(tokenize("dock panel dragged"))
        assert scores[0] > scores[1]

    def test_rank_lexical_only(self):
        entries = [
            {"id": "a", "description": "licence activation check on startup", "fix": "verify key", "files": ["Lic.cpp"]},
            {"id": "b", "description": "toolbar colour palette", "fix": "new colours", "files": ["Bar.cpp"]},
        ]
        r = rank(entries, "licence activation", semantic=False)
        assert r[0]["relevance"] > r[1]["relevance"] and r[0]["matched"] == 2


class TestPromptRecall:
    ENTRIES = [
        {"id": "grace001", "type": "bug_fix", "status": "active", "confidence": 0.6,
         "description": "Grace notes lost after quantize in bar processing",
         "cause": "attachGraceNotes ran before quantizeBar", "fix": "moved the call after quantization",
         "functions": ["attachGraceNotes", "quantizeBar"], "files": ["ScoreNoteInserter.cpp"],
         "tags": [], "timestamp": _ts(3)},
        {"id": "theme002", "type": "feature", "status": "active", "confidence": 0.6,
         "description": "Dark theme stylesheet reload without restart", "fix": "watch the qss file",
         "functions": ["reloadStyle"], "files": ["Theme.cpp"], "tags": [], "timestamp": _ts(2)},
        {"id": "arch003", "type": "note", "status": "active", "confidence": 0.3,
         "description": "old archived thing about grace notes quantize", "fix": "",
         "functions": [], "files": [], "tags": ["archived"], "timestamp": _ts(90)},
    ]

    def test_strong_match_and_silence(self):
        picked = select(self.ENTRIES, "fix grace notes quantize ordering", semantic=False)
        assert [e["id"] for e in picked["strong"]] == ["grace001"]   # archived one never shown
        assert select(self.ENTRIES, "так, продовжуй", semantic=False) == {"strong": [], "related": []}
        assert select(self.ENTRIES, "зроби commit і push", semantic=False)["strong"] == []

    def test_no_repeat_within_session_and_logging(self, tmp_path):
        st = Storage(tmp_path)
        st.write("memory.json", self.ENTRIES)
        first = recall_for_prompt(tmp_path, "p", "fix grace notes quantize ordering", "sess-1")
        assert "grace001" in first
        again = recall_for_prompt(tmp_path, "p", "grace notes quantize again please", "sess-1")
        assert again == ""                                           # already shown this session
        other = recall_for_prompt(tmp_path, "p", "fix grace notes quantize ordering", "sess-2")
        assert "grace001" in other
        events = [e for e in st.read("activity_log.json", []) if e["action"] == "inject_prompt_recall"]
        assert len(events) == 2 and events[0]["affected"] == ["grace001"]

    def test_storm_guard(self, tmp_path):
        st = Storage(tmp_path)
        st.write("memory.json", self.ENTRIES)
        for _ in range(15):
            st.append_log("activity_log.json", {"timestamp": _ts(0), "action": "inject_prompt_recall"})
        assert recall_for_prompt(tmp_path, "p", "fix grace notes quantize ordering", "s9") == ""


# ── injection ────────────────────────────────────────────────────────────────

class TestInjectionBudget:
    def _many(self, n=400):
        out = []
        for i in range(n):
            out.append({"id": f"id{i:05d}", "type": "decision" if i % 3 == 0 else "bug_fix",
                        "status": "active", "confidence": 0.5 + (i % 5) / 10,
                        "description": f"entry number {i} " + "with a long description " * 6,
                        "files": [f"Source/Module{i % 40}.cpp"], "tags": ["durable"] if i % 4 == 0 else [],
                        "timestamp": _ts(i % 60)})
        return out

    def test_fits_budget_and_keeps_footer(self):
        entries = self._many()
        entries.append({"id": "rule0001", "type": "rule", "status": "active", "confidence": 0.9,
                        "description": "Never change the public API without asking the client",
                        "tags": ["pinned"], "timestamp": _ts(50)})
        summary = _build_summary(entries, [], "big")
        assert len(summary) <= MAX_CHARS
        assert "### Stats" in summary and "Tip:" in summary          # tail is never cut off
        assert summary.index("### Rules (always apply)") < summary.index("### Key Decisions")
        assert "Never change the public API" in summary
        assert "### Recent Activity" in summary

    def test_compact_mode_keeps_rules_only(self):
        entries = self._many(50)
        entries.append({"id": "rule0001", "type": "rule", "status": "active", "confidence": 0.9,
                        "description": "Always run the linter before committing", "tags": ["pinned"],
                        "timestamp": _ts(5)})
        summary = _build_summary(entries, [], "p", compact=True)
        assert "Always run the linter" in summary
        assert "### Key Decisions" not in summary and "Compact injection" in summary
        assert len(summary) < 1200

    def test_shown_ids_reported(self):
        shown: list = []
        _build_summary(self._many(30), [], "p", shown_ids=shown)
        assert len(shown) >= 10 and all(i.startswith("id") for i in shown)
