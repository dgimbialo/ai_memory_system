"""Tests for improvement pack 2 (B+C+A+E+D):

A. birth-by-type confidence, usage extends half-life, recency tiebreak
E. conflict burst cap, fast triage tier
D. conservative auto-dedup (same-files requirement)
B is covered by manual smoke (injection logging is I/O glue).
"""
import sys
import os
import json
import pytest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.engine as engine_mod
from core.engine import MemoryEngine
from core.decay import entry_effective_confidence, USAGE_HALF_LIFE_BONUS
from core.deduplicator import Deduplicator
from core.conflict import ConflictRecord


def _ts(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ---------------------------------------------------------------------------
# A1: birth confidence by type
# ---------------------------------------------------------------------------

class TestBirthConfidence:
    def test_decision_born_high(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        r = eng.add_memory({"type": "decision", "description": "use sqlite everywhere"})
        assert r["entry"]["confidence"] == pytest.approx(0.75)

    def test_note_born_low(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        r = eng.add_memory({"type": "note", "description": "misc observation here"})
        assert r["entry"]["confidence"] == pytest.approx(0.45)

    def test_bug_fix_born_mid(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        r = eng.add_memory({"type": "bug_fix", "description": "fixed a crash somewhere",
                            "cause": "x", "fix": "y"})
        assert r["entry"]["confidence"] == pytest.approx(0.60)

    def test_explicit_confidence_respected(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        r = eng.add_memory({"type": "decision", "description": "explicit conf wins",
                            "confidence": 0.9})
        assert r["entry"]["confidence"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# A2: usage_count extends the half-life
# ---------------------------------------------------------------------------

class TestUsageHalfLifeBonus:
    def test_used_entry_decays_slower(self):
        now = datetime.now(timezone.utc)
        old = _ts(60)
        newer = sorted([old] + [_ts(1)] * 20)  # 60d effective age
        unused = {"confidence": 0.9, "timestamp": old, "type": "bug_fix"}
        used = {"confidence": 0.9, "timestamp": old, "type": "bug_fix", "usage_count": 2}
        eff_unused = entry_effective_confidence(unused, sorted_timestamps=newer, now=now)
        eff_used = entry_effective_confidence(used, sorted_timestamps=newer, now=now)
        assert eff_used > eff_unused
        # usage_count=2 -> hl x2 -> 60d is one half-life vs two
        assert eff_unused == pytest.approx(0.45, rel=0.05)
        assert eff_used == pytest.approx(0.9 * 0.5 ** (60 / (60 * (1 + 2 * USAGE_HALF_LIFE_BONUS))), rel=0.05)


# ---------------------------------------------------------------------------
# A4: recency tiebreak in query ranking
# ---------------------------------------------------------------------------

class TestRecencyTiebreak:
    def test_fresher_of_two_identical_ranks_first(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        eng.add_memory({"type": "note", "description": "midi timing quantize logic",
                        "confidence": 0.5})
        r2 = eng.add_memory({"type": "note", "description": "midi timing quantize logic",
                             "confidence": 0.5})
        # Backdate the first so the second is strictly fresher
        memory = eng._read_memory()
        for e in memory:
            if e["id"] != r2["entry"]["id"]:
                e["timestamp"] = _ts(30)
        eng.storage.write("memory.json", memory)
        results = eng.query_memory("midi timing quantize", top_k=2)
        assert results[0]["id"] == r2["entry"]["id"]


# ---------------------------------------------------------------------------
# E1: conflict burst cap
# ---------------------------------------------------------------------------

_DISTINCT_TOPICS = [
    "usb protocol framing on the acquisition device",
    "chart rendering colors in the dashboard",
    "midi quantization of grace notes",
    "http server threading and keepalive",
    "wiki markdown renderer for file pages",
    "confidence decay half life tuning",
    "conflict resolver merge action semantics",
    "embedding cache for semantic search",
    "session start hook for context injection",
    "revert detector hotspot guard rules",
    "settings form validation in the web ui",
    "dependency graph cycle detection",
    "activity log ring buffer streaming",
    "file summary digest generation",
    "gpib instrument waveform preamble parsing",
    "toolbar filters on the entries table",
    "translation strings for ukrainian locale",
    "backup rotation for atomic json writes",
    "auto tagging keywords dictionary",
    "test id traceability warnings",
    "linter checks for orphaned links",
    "daemon file watcher fallback polling",
    "copilot log scanning intervals",
    "dark theme palette accent colors",
    "dashboard kpi cards layout grid",
    "sse client queue overflow handling",
    "vs global mcp configuration file",
    "cursor ide config writer merge",
    "python venv interpreter resolution",
    "windows utf8 console reconfiguration",
]


class TestConflictBurstCap:
    def test_scan_records_at_most_cap(self, tmp_path, monkeypatch):
        eng = MemoryEngine(str(tmp_path))
        # Seed 30 clearly distinct entries (no accidental per-add conflicts)
        ids = []
        for topic in _DISTINCT_TOPICS:
            r = eng.add_memory({"type": "note", "description": topic,
                                "confidence": 0.5})
            ids.append(r["entry"]["id"])
        assert not eng._read_conflicts()  # sanity: seeding minted no conflicts

        fake = [
            ConflictRecord(
                entry_a=ids[i], entry_b=ids[i + 15],
                reason="fake", similarity=0.5 + i * 0.01,
            )
            for i in range(15)
        ]
        monkeypatch.setattr(engine_mod, "find_all_conflicts", lambda entries: fake)
        eng.detect_conflicts()

        recorded = [c for c in eng._read_conflicts() if not c.get("resolved")]
        assert len(recorded) <= eng._MAX_NEW_CONFLICTS_PER_SCAN
        # The strongest (highest similarity) survived the cap
        sims = sorted((c.get("similarity", 0) for c in recorded), reverse=True)
        assert sims[0] == pytest.approx(0.64, abs=0.01)

    def test_per_add_conflicts_capped(self, tmp_path):
        """A single add that matches many prior entries records at most
        _MAX_NEW_CONFLICTS_PER_ADD conflicts (strongest first)."""
        eng = MemoryEngine(str(tmp_path))
        for i in range(8):
            eng.add_memory({"type": "note",
                            "description": f"duplicate unresolved topic variant {i}: "
                                           "midi grace note pairing behaviour",
                            "confidence": 0.5})
        n = len(eng._read_conflicts())
        # 8 near-identical adds: without the cap this snowballs into dozens
        # (observed 278 from 30 adds); with it, each add records <= 3.
        assert n <= 8 * eng._MAX_NEW_CONFLICTS_PER_ADD


# ---------------------------------------------------------------------------
# E2: fast triage tier
# ---------------------------------------------------------------------------

class TestFastTriage:
    def _seed_conflict(self, eng, conf_a, conf_b, age_days):
        ra = eng.add_memory({"type": "note", "description": "side a of the dispute",
                             "confidence": conf_a})
        rb = eng.add_memory({"type": "note", "description": "side b of the dispute",
                             "confidence": conf_b})
        conflicts = eng._read_conflicts()
        conflicts.append({
            "id": "c-test-1",
            "entry_a": ra["entry"]["id"],
            "entry_b": rb["entry"]["id"],
            "reason": "test conflict",
            "similarity": 0.9,
            "timestamp": _ts(age_days),
        })
        eng.storage.write("conflicts.json", conflicts)

    def test_two_week_old_decayed_conflict_dismissed(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        self._seed_conflict(eng, 0.30, 0.35, age_days=15)
        result = eng.triage_conflicts(dry_run=True)
        assert result["dismissed_count"] == 1

    def test_young_conflict_kept(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        self._seed_conflict(eng, 0.30, 0.35, age_days=5)
        result = eng.triage_conflicts(dry_run=True)
        assert result["dismissed_count"] == 0

    def test_confident_sides_kept_at_two_weeks(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        self._seed_conflict(eng, 0.30, 0.60, age_days=15)
        result = eng.triage_conflicts(dry_run=True)
        assert result["dismissed_count"] == 0


# ---------------------------------------------------------------------------
# D: conservative auto-dedup (same files only)
# ---------------------------------------------------------------------------

class TestSameFilesDedup:
    def _twin(self, eng, files):
        return eng.add_memory({
            "type": "feature",
            "description": "TASK-99: identical dashboard server with graphs and tabs",
            "fix": "same implementation text repeated verbatim for the dedup test",
            "files": files,
            "confidence": 0.8,
        })

    def test_different_files_not_merged(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        self._twin(eng, ["a.py"])
        self._twin(eng, ["b.py"])
        result = Deduplicator(eng, 0.9).apply(dry_run=False, require_same_files=True)
        assert result["merged_count"] == 0

    def test_same_files_merged(self, tmp_path):
        eng = MemoryEngine(str(tmp_path))
        self._twin(eng, ["a.py"])
        self._twin(eng, ["a.py"])
        result = Deduplicator(eng, 0.9).apply(dry_run=False, require_same_files=True)
        assert result["merged_count"] == 1
        active = [e for e in eng._read_memory()
                  if e.get("status") == "active" and "TASK-99" in e.get("description", "")]
        assert len(active) == 1
