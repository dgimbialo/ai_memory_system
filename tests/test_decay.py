"""Tests for Confidence Decay."""
import sys
import os
import json
import math
import pytest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.decay import (
    effective_confidence, entry_effective_confidence, activity_age_days,
    DecayEngine, HALF_LIFE_DAYS, MIN_CONFIDENCE, GRACE_PERIOD_DAYS,
    DAYS_PER_EVENT, DECISION_HALF_LIFE_MULT,
)
from core.engine import MemoryEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(tmp_path: str) -> MemoryEngine:
    return MemoryEngine(tmp_path)


def _ts(days_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


_CTR = [0]


def _add(engine: MemoryEngine, description: str = "test",
         confidence: float = 0.9,
         days_ago: float = 0) -> str:
    _CTR[0] += 1
    uid = _CTR[0]
    payload = {
        "type": "bug_fix",
        "description": "{} uid={}".format(description, uid),
        "cause": "c{}".format(uid),
        "fix": "f{}".format(uid),
        "files": ["f{}.cpp".format(uid)],
        "functions": ["Fn{}".format(uid)],
        "decisions": [],
        "confidence": confidence,
        "tags": ["test"],
    }
    result = engine.add_memory(payload)
    eid = result["entry"]["id"]
    # Backdate timestamp if requested
    if days_ago > 0:
        memory = engine._read_memory()
        for e in memory:
            if e["id"] == eid:
                e["timestamp"] = _ts(days_ago)
                break
        engine.storage.write("memory.json", memory)
    return eid


def _add_activity(engine: MemoryEngine, n: int) -> None:
    """Add n fresh entries: decay is activity-relative, so an old entry only
    ages when the project has moved on (each newer entry = DAYS_PER_EVENT)."""
    for _ in range(n):
        _add(engine, description="activity", confidence=0.9, days_ago=0)


# ---------------------------------------------------------------------------
# Unit tests: effective_confidence()
# ---------------------------------------------------------------------------

class TestEffectiveConfidenceFormula:
    def test_fresh_entry_no_decay(self):
        ts = datetime.now(timezone.utc).isoformat()
        assert effective_confidence(0.9, ts) == pytest.approx(0.9, rel=1e-4)

    def test_grace_period_no_decay(self):
        """Entries younger than GRACE_PERIOD_DAYS must not decay."""
        ts = _ts(GRACE_PERIOD_DAYS - 1)
        assert effective_confidence(0.9, ts) == pytest.approx(0.9, rel=1e-4)

    def test_half_life_halves_confidence(self):
        ts = _ts(HALF_LIFE_DAYS)
        eff = effective_confidence(0.9, ts)
        expected = 0.9 * 0.5
        assert eff == pytest.approx(expected, rel=0.02)

    def test_two_half_lives(self):
        ts = _ts(HALF_LIFE_DAYS * 2)
        eff = effective_confidence(0.9, ts)
        expected = max(0.9 * 0.25, MIN_CONFIDENCE)
        assert eff == pytest.approx(expected, rel=0.02)

    def test_floor_applied(self):
        """Very old entry must not go below MIN_CONFIDENCE."""
        ts = _ts(365)
        eff = effective_confidence(0.9, ts)
        assert eff >= MIN_CONFIDENCE

    def test_floor_exact(self):
        ts = _ts(365)
        eff = effective_confidence(0.9, ts, min_confidence=0.40)
        assert eff == pytest.approx(0.40, rel=1e-4)

    def test_custom_half_life(self):
        ts = _ts(30)
        eff = effective_confidence(1.0, ts, half_life_days=30)
        assert eff == pytest.approx(0.5, rel=0.02)

    def test_custom_min_confidence(self):
        ts = _ts(500)
        eff = effective_confidence(0.9, ts, min_confidence=0.6)
        assert eff == pytest.approx(0.6, rel=1e-4)

    def test_invalid_timestamp_returns_original(self):
        assert effective_confidence(0.7, "not-a-date") == pytest.approx(0.7)

    def test_injectable_now(self):
        """Injecting 'now' allows deterministic testing."""
        fixed_ts = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
        now_60_days_later = datetime(2026, 3, 2, tzinfo=timezone.utc)
        eff = effective_confidence(0.8, fixed_ts, now=now_60_days_later)
        expected = max(0.8 * 0.5, MIN_CONFIDENCE)
        assert eff == pytest.approx(expected, rel=0.02)

    def test_zero_confidence_stays_zero(self):
        ts = _ts(90)
        assert effective_confidence(0.0, ts) == pytest.approx(MIN_CONFIDENCE)


# ---------------------------------------------------------------------------
# Unit tests: DecayEngine.preview()
# ---------------------------------------------------------------------------

class TestDecayEnginePreview:
    def test_fresh_entry_not_changed(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        _add(engine, days_ago=0)
        rows = DecayEngine(engine).preview()
        assert all(not r["changed"] for r in rows)

    def test_old_entry_marked_changed(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        eid = _add(engine, confidence=0.9, days_ago=90)
        _add_activity(engine, 4)  # project moved on -> old entry ages
        rows = DecayEngine(engine).preview()
        changed = [r for r in rows if r["changed"] and r["id"] == eid]
        assert len(changed) == 1
        assert changed[0]["effective_confidence"] < changed[0]["original_confidence"]
        assert changed[0]["effective_confidence"] >= MIN_CONFIDENCE

    def test_dormant_project_freezes_decay(self, tmp_path):
        """An old entry in a paused project must NOT decay: no newer entries
        means nothing has superseded the memory (activity-relative aging)."""
        engine = _make_engine(str(tmp_path))
        eid = _add(engine, confidence=0.9, days_ago=90)
        rows = DecayEngine(engine).preview()
        row = next(r for r in rows if r["id"] == eid)
        assert row["changed"] is False
        assert row["effective_confidence"] == pytest.approx(0.9, rel=1e-4)

    def test_preview_does_not_write(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        eid = _add(engine, confidence=0.9, days_ago=90)
        DecayEngine(engine).preview()
        # Stored confidence must be unchanged
        mem = {e["id"]: e for e in engine._read_memory()}
        assert mem[eid]["confidence"] == pytest.approx(0.9)

    def test_superseded_entry_included_in_preview(self, tmp_path):
        """preview() includes all entries; decay is shown but apply() skips them."""
        engine = _make_engine(str(tmp_path))
        eid = _add(engine, confidence=0.9, days_ago=90)
        engine.update_status(eid, "superseded")
        rows = DecayEngine(engine).preview()
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# Unit tests: DecayEngine.apply()
# ---------------------------------------------------------------------------

class TestDecayEngineApply:
    def test_dry_run_no_write(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        eid = _add(engine, confidence=0.9, days_ago=90)
        _add_activity(engine, 4)
        result = DecayEngine(engine).apply(dry_run=True)
        assert result["dry_run"] is True
        assert result["would_change"] >= 1
        assert result["changed_count"] == 0
        # Stored value must be unchanged
        mem = {e["id"]: e for e in engine._read_memory()}
        assert mem[eid]["confidence"] == pytest.approx(0.9)

    def test_apply_writes_decayed_confidence(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        eid = _add(engine, confidence=0.9, days_ago=90)
        _add_activity(engine, 4)
        result = DecayEngine(engine).apply(dry_run=False)
        assert result["changed_count"] >= 1
        mem = {e["id"]: e for e in engine._read_memory()}
        assert mem[eid]["confidence"] < 0.9
        assert mem[eid]["confidence"] >= MIN_CONFIDENCE

    def test_apply_skips_superseded(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        eid = _add(engine, confidence=0.9, days_ago=90)
        engine.update_status(eid, "superseded")
        result = DecayEngine(engine).apply(dry_run=False)
        assert result["skipped_count"] >= 1
        mem = {e["id"]: e for e in engine._read_memory()}
        assert mem[eid]["confidence"] == pytest.approx(0.9)

    def test_apply_fresh_entry_not_changed(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        eid = _add(engine, confidence=0.9, days_ago=0)
        result = DecayEngine(engine).apply(dry_run=False)
        assert result["changed_count"] == 0
        mem = {e["id"]: e for e in engine._read_memory()}
        assert mem[eid]["confidence"] == pytest.approx(0.9)

    def test_apply_floor_respected(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        eid = _add(engine, confidence=0.9, days_ago=500)
        DecayEngine(engine).apply(dry_run=False)
        mem = {e["id"]: e for e in engine._read_memory()}
        assert mem[eid]["confidence"] >= MIN_CONFIDENCE

    def test_apply_logs_action(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        _add(engine, confidence=0.9, days_ago=90)
        _add_activity(engine, 4)
        DecayEngine(engine).apply(dry_run=False)
        log = engine.storage.read("activity_log.json", default=[])
        decay_logs = [l for l in log if l.get("action") == "decay_confidence"]
        assert len(decay_logs) >= 1

    def test_apply_idempotent_after_floor(self, tmp_path):
        """Calling apply() twice on floored entry changes nothing the second time."""
        engine = _make_engine(str(tmp_path))
        eid = _add(engine, confidence=0.26, days_ago=500)
        _add_activity(engine, 4)  # effective age 12d -> 0.26 decays to floor
        DecayEngine(engine).apply(dry_run=False)
        conf_after_first = {e["id"]: e for e in engine._read_memory()}[eid]["confidence"]
        assert conf_after_first == pytest.approx(MIN_CONFIDENCE)
        DecayEngine(engine).apply(dry_run=False)
        conf_after_second = {e["id"]: e for e in engine._read_memory()}[eid]["confidence"]
        assert conf_after_first == pytest.approx(conf_after_second)

    def test_custom_params(self, tmp_path):
        """Decay with half_life=30 decays faster than default."""
        engine = _make_engine(str(tmp_path))
        eid = _add(engine, confidence=0.9, days_ago=30)
        _add_activity(engine, 10)  # 10 events x 3d = 30d effective age (= wall age)
        DecayEngine(engine, half_life_days=30, min_confidence=0.1).apply(dry_run=False)
        mem = {e["id"]: e for e in engine._read_memory()}
        assert mem[eid]["confidence"] == pytest.approx(0.45, rel=0.05)

    def test_decision_decays_slower(self, tmp_path):
        """Decisions age DECISION_HALF_LIFE_MULT times slower than bug fixes."""
        engine = _make_engine(str(tmp_path))
        bug_id = _add(engine, confidence=0.9, days_ago=60)
        memory = engine._read_memory()
        # Clone the same age/confidence as a decision entry
        dec_id = _add(engine, description="a design decision", confidence=0.9, days_ago=60)
        memory = engine._read_memory()
        for e in memory:
            if e["id"] == dec_id:
                e["type"] = "decision"
        engine.storage.write("memory.json", memory)
        _add_activity(engine, 20)  # 60d effective age for both

        rows = {r["id"]: r for r in DecayEngine(engine).preview()}
        assert rows[dec_id]["effective_confidence"] > rows[bug_id]["effective_confidence"]


# ---------------------------------------------------------------------------
# Unit tests: activity-relative aging primitives
# ---------------------------------------------------------------------------

class TestActivityRelativeAging:
    def test_no_newer_events_zero_age(self):
        assert activity_age_days(wall_age_days=300, newer_events=0) == 0.0

    def test_age_capped_by_wall_clock(self):
        """A burst of activity can't make memory older than it really is."""
        assert activity_age_days(wall_age_days=10, newer_events=100) == 10.0

    def test_scales_with_events(self):
        assert activity_age_days(wall_age_days=300, newer_events=5) == pytest.approx(
            5 * DAYS_PER_EVENT
        )

    def test_entry_effective_confidence_uses_activity(self):
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(days=200)).isoformat()
        entry = {"confidence": 0.9, "timestamp": old_ts, "type": "bug_fix"}
        # Store contains only this entry -> no newer events -> no decay
        eff = entry_effective_confidence(entry, sorted_timestamps=[old_ts], now=now)
        assert eff == pytest.approx(0.9, rel=1e-4)
        # Same entry with 20 newer timestamps -> 60d effective age -> halved
        newer = sorted([old_ts] + [(now - timedelta(days=1)).isoformat()] * 20)
        eff2 = entry_effective_confidence(entry, sorted_timestamps=newer, now=now)
        assert eff2 == pytest.approx(0.45, rel=0.05)


# ---------------------------------------------------------------------------
# Integration: update_instructions uses decayed confidence
# ---------------------------------------------------------------------------

class TestUpdateInstructionsDecayFilter:
    def test_old_low_confidence_excluded(self, tmp_path):
        """Entry with original conf=0.9 but 180 days old should decay below 0.80
        and be excluded from Learned Patterns."""
        engine = _make_engine(str(tmp_path))
        eid = _add(engine, confidence=0.9, days_ago=180)
        # Give it a decision
        memory = engine._read_memory()
        for e in memory:
            if e["id"] == eid:
                e["decisions"] = ["Old decision that should be excluded"]
                break
        engine.storage.write("memory.json", memory)

        # After 180 days: 0.9 * 0.5^(180/60) = 0.9 * 0.125 = 0.1125 < MIN(0.40) -> 0.40
        # 0.40 < min_confidence=0.80 -> excluded
        result = engine.update_instructions(
            project_path=str(tmp_path),
            min_confidence=0.80,
            dry_run=True,
        )
        content = result.get("content", "")
        assert "Old decision that should be excluded" not in content

    def test_fresh_high_confidence_included(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        eid = _add(engine, confidence=0.95, days_ago=0)
        memory = engine._read_memory()
        for e in memory:
            if e["id"] == eid:
                e["decisions"] = ["Fresh important decision to include"]
                break
        engine.storage.write("memory.json", memory)

        result = engine.update_instructions(
            project_path=str(tmp_path),
            min_confidence=0.80,
            dry_run=True,
        )
        content = result.get("content", "")
        assert "Fresh important decision to include" in content


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestDecayCLI:
    def test_decay_dry_run(self, tmp_path):
        import subprocess
        engine = _make_engine(str(tmp_path))
        _add(engine, confidence=0.9, days_ago=90)
        _add_activity(engine, 4)

        result = subprocess.run(
            ["c:/python313/python.exe",
             "D:/ai_memory_system/run.py",
             "--project", str(tmp_path),
             "decay", "--dry-run"],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["dry_run"] is True
        assert data["would_change"] >= 1
        assert data["changed_count"] == 0

    def test_decay_apply(self, tmp_path):
        import subprocess
        engine = _make_engine(str(tmp_path))
        eid = _add(engine, confidence=0.9, days_ago=90)
        _add_activity(engine, 4)

        result = subprocess.run(
            ["c:/python313/python.exe",
             "D:/ai_memory_system/run.py",
             "--project", str(tmp_path),
             "decay"],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["changed_count"] >= 1

    def test_decay_preview(self, tmp_path):
        import subprocess
        engine = _make_engine(str(tmp_path))
        _add(engine, confidence=0.9, days_ago=90)

        result = subprocess.run(
            ["c:/python313/python.exe",
             "D:/ai_memory_system/run.py",
             "--project", str(tmp_path),
             "decay_preview"],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        # --all not passed: only changed entries shown
        assert all(r["changed"] for r in data)

    def test_decay_preview_all(self, tmp_path):
        import subprocess
        engine = _make_engine(str(tmp_path))
        _add(engine, confidence=0.9, days_ago=0)   # fresh, not changed
        _add(engine, confidence=0.9, days_ago=90)  # old, changed

        result = subprocess.run(
            ["c:/python313/python.exe",
             "D:/ai_memory_system/run.py",
             "--project", str(tmp_path),
             "decay_preview", "--all"],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        # All entries returned
        assert len(data) >= 2
