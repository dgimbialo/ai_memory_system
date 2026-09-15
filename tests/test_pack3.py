"""Tests for improvement pack 3:

P1 durable-knowledge tagging + slow decay
P2 duplicate conflicts auto-merged, conflict-status entries visible in injection
P3 per-prompt recall scorer
P4 archive tier + revival on touch, read-driven maintenance
"""
import sys
import os
import json
import pytest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.engine import MemoryEngine
from core.decay import entry_effective_confidence
from core.context_injector import _build_summary
from core.prompt_recall import _score, _tokens


def _ts(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ---------------------------------------------------------------------------
# P1: durable knowledge
# ---------------------------------------------------------------------------

class TestDurableKnowledge:
    def test_architecture_note_gets_durable_tag(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        r = eng.add_memory({"type": "note",
                            "description": "ARCHITECTURE: SystemStaffID and PartID share one number space"})
        assert "durable" in r["entry"]["tags"]

    def test_root_cause_gets_durable_tag(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        r = eng.add_memory({"type": "note",
                            "description": "Task 1145 investigation results",
                            "cause": "root cause found in dialog init order"})
        assert "durable" in r["entry"]["tags"]

    def test_plain_note_not_tagged(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        r = eng.add_memory({"type": "note",
                            "description": "tried a different color for the toolbar"})
        assert "durable" not in r["entry"]["tags"]

    def test_durable_decays_like_decision(self):
        now = datetime.now(timezone.utc)
        old = _ts(60)
        newer = sorted([old] + [_ts(1)] * 20)  # 60d effective age
        plain = {"confidence": 0.9, "timestamp": old, "type": "note"}
        durable = {"confidence": 0.9, "timestamp": old, "type": "note",
                   "tags": ["durable"]}
        decision = {"confidence": 0.9, "timestamp": old, "type": "decision"}
        eff_plain = entry_effective_confidence(plain, sorted_timestamps=newer, now=now)
        eff_durable = entry_effective_confidence(durable, sorted_timestamps=newer, now=now)
        eff_decision = entry_effective_confidence(decision, sorted_timestamps=newer, now=now)
        assert eff_durable > eff_plain
        assert eff_durable == pytest.approx(eff_decision, rel=1e-6)


# ---------------------------------------------------------------------------
# P2: duplicate conflicts auto-merge; conflict entries visible in injection
# ---------------------------------------------------------------------------

class TestDuplicateAutoMerge:
    def test_verbatim_duplicate_is_merged_not_quarantined(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        payload = {"type": "note",
                   "description": "Organize Staves dialog labels combo items by Part ID",
                   "fix": "resolved the label lookup through the part table",
                   "files": ["CDlgStaffConnect.cpp"], "confidence": 0.5}
        eng.add_memory(payload)
        r2 = eng.add_memory(dict(payload))
        # The duplicate pair must end merged: one active canonical entry,
        # no open duplicate conflict, no entry stuck in status=conflict.
        assert r2.get("auto_merged"), "expected auto-merge to fire"
        open_c = [c for c in eng._read_conflicts() if not c.get("resolved")]
        assert open_c == []
        mem = eng._read_memory()
        active_dupes = [e for e in mem
                        if e.get("status") == "active" and "Organize Staves" in e.get("description", "")]
        assert len(active_dupes) == 1
        assert not any(e.get("status") == "conflict" for e in mem)

    def test_injection_shows_conflict_status_entries(self, tmp_path):
        entries = [
            {"id": "aa11", "type": "note", "status": "conflict",
             "description": "important knowledge held hostage by a conflict",
             "confidence": 0.55, "timestamp": _ts(1), "tags": [], "files": []},
        ]
        summary = _build_summary(entries, [], "proj")
        assert "held hostage" in summary
        assert "in-conflict" in summary

    def test_injection_hides_archived(self, tmp_path):
        entries = [
            {"id": "bb22", "type": "note", "status": "active",
             "description": "archived stale knowledge nobody used",
             "confidence": 0.25, "timestamp": _ts(90),
             "tags": ["archived"], "files": []},
            {"id": "cc33", "type": "note", "status": "active",
             "description": "fresh living knowledge", "confidence": 0.5,
             "timestamp": _ts(1), "tags": [], "files": []},
        ]
        summary = _build_summary(entries, [], "proj")
        assert "archived stale knowledge" not in summary
        assert "fresh living knowledge" in summary


# ---------------------------------------------------------------------------
# P3: prompt recall scorer
# ---------------------------------------------------------------------------

class TestPromptRecallScorer:
    ENTRY = {
        "description": "Grace notes lost after quantize in bar processing",
        "cause": "attachGraceNotes ran before quantizeBar",
        "fix": "moved the call after quantization",
        "functions": ["attachGraceNotes", "quantizeBar"],
        "files": ["ScoreNoteInserter.cpp"],
        "tags": ["midi"],
    }

    def test_relevant_prompt_scores_high(self):
        q = _tokens("fix grace note pairing in attachGraceNotes")
        score, distinct = _score(self.ENTRY, q)
        assert score >= 5 and distinct >= 2

    def test_symbol_match_weighs_most(self):
        q_sym = _tokens("attachGraceNotes broken")
        q_word = _tokens("grace broken")
        s_sym, _ = _score(self.ENTRY, q_sym)
        s_word, _ = _score(self.ENTRY, q_word)
        assert s_sym > s_word

    def test_unrelated_prompt_scores_low(self):
        q = _tokens("update the dashboard color palette")
        score, distinct = _score(self.ENTRY, q)
        assert score < 5

    def test_short_prompt_has_no_tokens(self):
        assert len(_tokens("yes")) == 0


# ---------------------------------------------------------------------------
# P4: archive tier
# ---------------------------------------------------------------------------

class TestArchiveTier:
    def _stale_entry(self, eng, **over):
        r = eng.add_memory({"type": "note", "description": "old floor entry nobody uses",
                            "confidence": 0.25, **over})
        eid = r["entry"]["id"]
        mem = eng._read_memory()
        for e in mem:
            if e["id"] == eid:
                e["timestamp"] = _ts(45)
                e.update(over)
        eng.storage.write("memory.json", mem)
        return eid

    def test_stale_floor_entry_archived(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        eid = self._stale_entry(eng)
        n = eng._archive_stale_entries()
        assert n == 1
        e = next(x for x in eng._read_memory() if x["id"] == eid)
        assert "archived" in e["tags"]

    def test_durable_never_archived(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        eid = self._stale_entry(eng)
        mem = eng._read_memory()
        for e in mem:
            if e["id"] == eid:
                e["tags"] = list(e.get("tags") or []) + ["durable"]
        eng.storage.write("memory.json", mem)
        assert eng._archive_stale_entries() == 0

    def test_touch_revives_archived(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        eid = self._stale_entry(eng)
        eng._archive_stale_entries()
        eng.touch_used([eid])
        e = next(x for x in eng._read_memory() if x["id"] == eid)
        assert "archived" not in e["tags"]

    def test_fresh_entry_not_archived(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        eng.add_memory({"type": "note", "description": "fresh low-confidence entry",
                        "confidence": 0.25})
        assert eng._archive_stale_entries() == 0
