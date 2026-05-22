"""Tests for ConflictResolver."""
import sys
import os
import json
import uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.engine import MemoryEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(tmp_path: str) -> MemoryEngine:
    return MemoryEngine(tmp_path)


_COUNTER = [0]


def _add(engine: MemoryEngine, description: str, type_: str = "bug_fix",
         files=None, functions=None, decisions=None) -> str:
    """Add an entry with a unique description/files/functions to avoid
    triggering auto-conflict detection between unrelated test entries."""
    _COUNTER[0] += 1
    uid = _COUNTER[0]
    result = engine.add_memory({
        "type": type_,
        "description": "{} [uid={}]".format(description, uid),
        "cause": "test cause uid={}".format(uid),
        "fix": "test fix uid={}".format(uid),
        "files": files or ["file_{}.cpp".format(uid)],
        "functions": functions or ["Func_{}".format(uid)],
        "decisions": decisions or [],
        "confidence": 0.9,
        "tags": ["test"],
    })
    return result["entry"]["id"]


def _inject_conflict(engine: MemoryEngine, id_a: str, id_b: str,
                     reason: str = "test conflict") -> str:
    """Directly write a conflict record to conflicts.json and return its id."""
    cid = uuid.uuid4().hex[:12]
    conflicts = engine._read_conflicts()
    conflicts.append({
        "id": cid,
        "entry_a": id_a,
        "entry_b": id_b,
        "reason": reason,
        "similarity": 0.75,
        "timestamp": "2026-05-16T00:00:00+00:00",
    })
    engine.storage.write("conflicts.json", conflicts)
    return cid


# ---------------------------------------------------------------------------
# Tests: list_conflicts
# ---------------------------------------------------------------------------

class TestListConflicts:
    def test_empty(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        assert engine.list_conflicts() == []

    def test_returns_enriched_conflict(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        id_a = _add(engine, "Grace note placement at bar boundary")
        id_b = _add(engine, "MIDI channel drone pitch filter")
        cid = _inject_conflict(engine, id_a, id_b)

        conflicts = engine.list_conflicts()
        match = [c for c in conflicts if c["id"] == cid]
        assert len(match) == 1
        c = match[0]
        assert c["entry_a"]["id"] == id_a
        assert c["entry_b"]["id"] == id_b
        assert "reason" in c
        assert "similarity" in c

    def test_superseded_entry_excluded(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        id_a = _add(engine, "Tick space quantization rule")
        id_b = _add(engine, "Phase offset detection halfBeat")
        cid = _inject_conflict(engine, id_a, id_b)
        engine.update_status(id_a, "superseded", "manual test")

        # Conflict with superseded entry_a should be hidden
        matches = [c for c in engine.list_conflicts() if c["id"] == cid]
        assert len(matches) == 0


# ---------------------------------------------------------------------------
# Tests: supersede_a / supersede_b
# ---------------------------------------------------------------------------

class TestResolveSupersede:
    def test_supersede_a(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        id_a = _add(engine, "Old proportional fit algorithm")
        id_b = _add(engine, "New correct PPQN rounding approach")
        cid = _inject_conflict(engine, id_a, id_b)

        result = engine.resolve_conflict(cid, "supersede_a", "entry_b is correct")
        assert result["action"] == "supersede_a"
        assert id_a in result["affected_entries"]

        mem = {e["id"]: e for e in engine._read_memory()}
        assert mem[id_a]["status"] == "superseded"
        assert mem[id_b]["status"] != "superseded"

        remaining = [c for c in engine._read_conflicts() if c.get("id") == cid]
        assert remaining == []

    def test_supersede_b(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        id_a = _add(engine, "Correct bar tick calculation")
        id_b = _add(engine, "Wrong bar tick calculation variant")
        cid = _inject_conflict(engine, id_a, id_b)

        engine.resolve_conflict(cid, "supersede_b", "entry_a is correct")

        mem = {e["id"]: e for e in engine._read_memory()}
        assert mem[id_b]["status"] == "superseded"
        assert mem[id_a]["status"] != "superseded"

        remaining = [c for c in engine._read_conflicts() if c.get("id") == cid]
        assert remaining == []


# ---------------------------------------------------------------------------
# Tests: merge
# ---------------------------------------------------------------------------

class TestResolveMerge:
    def test_merge_creates_new_entry(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        id_a = _add(engine, "Grace group atomicity variant A",
                    files=["ScoreNoteInserter.cpp"],
                    functions=["OnBarBoundary"],
                    decisions=["Grace must always migrate together"])
        id_b = _add(engine, "Grace group atomicity variant B",
                    files=["BarPostProcessor.cpp"],
                    functions=["RunPostProcess"],
                    decisions=["Grace group is atomic: all graces plus melody same bar"])
        cid = _inject_conflict(engine, id_a, id_b)

        result = engine.resolve_conflict(cid, "merge", "canonical grace group decision")
        assert result["merged_entry"] is not None
        merged_id = result["merged_entry"]["id"]
        assert merged_id not in (id_a, id_b)

        mem = {e["id"]: e for e in engine._read_memory()}
        assert mem[id_a]["status"] == "superseded"
        assert mem[id_b]["status"] == "superseded"
        assert mem[merged_id]["status"] == "active"

        # Decisions are merged (union)
        assert len(mem[merged_id]["decisions"]) == 2
        # Files are merged
        assert "ScoreNoteInserter.cpp" in mem[merged_id]["files"]
        assert "BarPostProcessor.cpp" in mem[merged_id]["files"]
        # Tagged as merged
        assert "merged" in mem[merged_id]["tags"]

    def test_merge_conflict_removed(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        id_a = _add(engine, "Proportional quantization fit rule")
        id_b = _add(engine, "Drone MIDI note channel filter")
        cid = _inject_conflict(engine, id_a, id_b)
        engine.resolve_conflict(cid, "merge")
        remaining = [c for c in engine._read_conflicts() if c.get("id") == cid]
        assert remaining == []


# ---------------------------------------------------------------------------
# Tests: dismiss
# ---------------------------------------------------------------------------

class TestResolveDismiss:
    def test_dismiss_removes_conflict_only(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        id_a = _add(engine, "Phase offset detection mechanism")
        id_b = _add(engine, "Cross-bar tail calculation logic")
        cid = _inject_conflict(engine, id_a, id_b)

        result = engine.resolve_conflict(cid, "dismiss", "false positive")
        assert result["action"] == "dismiss"
        # dismiss restores both entries to active — affected_entries contains both
        assert set(result["affected_entries"]) == {id_a, id_b}

        remaining = [c for c in engine._read_conflicts() if c.get("id") == cid]
        assert remaining == []

        # Both entries must be active (not superseded, not stuck in conflict)
        mem = {e["id"]: e for e in engine._read_memory()}
        assert mem[id_a]["status"] == "active"
        assert mem[id_b]["status"] == "active"


# ---------------------------------------------------------------------------
# Tests: error handling
# ---------------------------------------------------------------------------

class TestResolveErrors:
    def test_invalid_action(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        with pytest.raises(ValueError, match="Invalid action"):
            engine.resolve_conflict("nonexistent", "bad_action")

    def test_missing_conflict_id(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        with pytest.raises(KeyError):
            engine.resolve_conflict("nonexistent_id", "dismiss")


# ---------------------------------------------------------------------------
# Tests: CLI integration
# ---------------------------------------------------------------------------

class TestListConflictsCommand:
    def test_cli_list_conflicts_json(self, tmp_path):
        """CLI must output valid JSON (even empty list) and include injected conflict."""
        import subprocess
        engine = _make_engine(str(tmp_path))
        id_a = _add(engine, "Grace note bar entry alpha")
        id_b = _add(engine, "Drone MIDI pitch filter beta")
        cid = _inject_conflict(engine, id_a, id_b, "test conflict cli")

        result = subprocess.run(
            ["c:/python313/python.exe",
             "D:/ai_memory_system/run.py",
             "--project", str(tmp_path),
             "list_conflicts"],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        ids = [c["id"] for c in data]
        assert cid in ids

    def test_cli_resolve_conflict(self, tmp_path):
        """CLI resolve_conflict supersede_a must mark entry_a superseded."""
        import subprocess
        engine = _make_engine(str(tmp_path))
        id_a = _add(engine, "Old tick offset approach gamma")
        id_b = _add(engine, "New PPQN normalization delta")
        cid = _inject_conflict(engine, id_a, id_b, "test resolve cli")

        result = subprocess.run(
            ["c:/python313/python.exe",
             "D:/ai_memory_system/run.py",
             "--project", str(tmp_path),
             "resolve_conflict",
             "--id", cid,
             "--action", "supersede_a",
             "--reason", "cli test supersede"],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["action"] == "supersede_a"
        assert id_a in data["affected_entries"]

        # Verify in storage
        mem = {e["id"]: e for e in engine._read_memory()}
        assert mem[id_a]["status"] == "superseded"
