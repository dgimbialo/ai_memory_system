"""Tests for RevertDetector."""
import sys
import os
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.engine import MemoryEngine
from core.revert_detector import RevertDetector, UNSTABLE_THRESHOLD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(tmp_path: str) -> MemoryEngine:
    return MemoryEngine(tmp_path)


_CTR = [0]


def _add(engine: MemoryEngine, description: str, fix: str = "",
         functions=None, files=None, type_: str = "bug_fix") -> dict:
    _CTR[0] += 1
    uid = _CTR[0]
    result = engine.add_memory({
        "type": type_,
        "description": description,
        "cause": "test uid={}".format(uid),
        "fix": fix or "test fix uid={}".format(uid),
        "files": files or ["ScoreNoteInserter_{}.cpp".format(uid)],
        "functions": functions or ["Func_{}".format(uid)],
        "decisions": [],
        "confidence": 0.9,
        "tags": ["test"],
    })
    return result


# ---------------------------------------------------------------------------
# Unit tests: RevertDetector.check()
# ---------------------------------------------------------------------------

class TestRevertDetectorNoWarning:
    """Cases where no warning should be emitted."""

    def test_no_shared_functions(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        entries = [
            {"id": "a1", "description": "add provisional render",
             "cause": "", "fix": "", "functions": ["FuncA"], "files": [],
             "status": "active", "tags": []},
            {"id": "a2", "description": "remove provisional render",
             "cause": "", "fix": "", "functions": ["FuncB"], "files": [],
             "status": "active", "tags": []},
        ]
        new_entry = {"id": "a3", "description": "add provisional render again",
                     "cause": "", "fix": "", "functions": ["FuncC"], "files": [],
                     "status": "active", "tags": []}
        detector = RevertDetector(engine)
        result = detector.check(new_entry, entries + [new_entry])
        assert result is None

    def test_only_adds_no_reverts(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        fn = ["processbar"]
        entries = [
            {"id": "b1", "description": "add batch mode",
             "cause": "", "fix": "implement batch processing",
             "functions": fn, "files": [], "status": "active", "tags": []},
            {"id": "b2", "description": "add batch mode v2",
             "cause": "", "fix": "enable batch v2",
             "functions": fn, "files": [], "status": "active", "tags": []},
        ]
        new_entry = {"id": "b3", "description": "add batch mode v3",
                     "cause": "", "fix": "create batch v3",
                     "functions": fn, "files": [], "status": "active", "tags": []}
        detector = RevertDetector(engine)
        result = detector.check(new_entry, entries + [new_entry])
        assert result is None

    def test_below_threshold(self, tmp_path):
        """One add + one revert = 1 pair, below UNSTABLE_THRESHOLD=2."""
        engine = _make_engine(str(tmp_path))
        fn = ["provisionalrender"]
        entries = [
            {"id": "c1", "description": "add provisional render",
             "cause": "", "fix": "implement render",
             "functions": fn, "files": [], "status": "active", "tags": []},
            {"id": "c2", "description": "revert provisional render",
             "cause": "", "fix": "remove render",
             "functions": fn, "files": [], "status": "active", "tags": []},
        ]
        # New entry is a second add � only 1 revert, so still 1 pair
        new_entry = {"id": "c3", "description": "add provisional render again",
                     "cause": "", "fix": "implement render again",
                     "functions": fn, "files": [], "status": "active", "tags": []}
        detector = RevertDetector(engine)
        result = detector.check(new_entry, entries + [new_entry])
        # 2 adds vs 1 revert = min(2,1)=1 pair < threshold(2)
        assert result is None

    def test_superseded_entries_ignored(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        fn = ["processnoteoff"]
        entries = [
            {"id": "d1", "description": "add feature X",
             "cause": "", "fix": "implement X",
             "functions": fn, "files": [], "status": "superseded", "tags": []},
            {"id": "d2", "description": "revert feature X",
             "cause": "", "fix": "remove X",
             "functions": fn, "files": [], "status": "superseded", "tags": []},
            {"id": "d3", "description": "revert feature X again",
             "cause": "", "fix": "disable X",
             "functions": fn, "files": [], "status": "superseded", "tags": []},
        ]
        new_entry = {"id": "d4", "description": "add feature X v4",
                     "cause": "", "fix": "enable X v4",
                     "functions": fn, "files": [], "status": "active", "tags": []}
        detector = RevertDetector(engine)
        result = detector.check(new_entry, entries + [new_entry])
        assert result is None


class TestRevertDetectorWarning:
    """Cases where a warning should be emitted."""

    def test_two_add_two_revert_same_function(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        fn = ["processbtchbarinner"]
        entries = [
            {"id": "e1", "description": "add provisional render batch",
             "cause": "", "fix": "implement provisional render",
             "functions": fn, "files": [], "status": "active", "tags": []},
            {"id": "e2", "description": "revert provisional render batch",
             "cause": "", "fix": "remove provisional render",
             "functions": fn, "files": [], "status": "active", "tags": []},
            {"id": "e3", "description": "add provisional render batch v2",
             "cause": "", "fix": "enable provisional render again",
             "functions": fn, "files": [], "status": "active", "tags": []},
        ]
        new_entry = {"id": "e4", "description": "revert provisional render v2",
                     "cause": "", "fix": "disable provisional render v2",
                     "functions": fn, "files": [], "status": "active", "tags": []}
        detector = RevertDetector(engine)
        result = detector.check(new_entry, entries + [new_entry])
        assert result is not None
        assert result.revert_count >= UNSTABLE_THRESHOLD
        assert "processbtchbarinner" in result.unstable_functions

    def test_warning_contains_all_related_ids(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        fn = ["flushbatchqueue"]
        entries = [
            {"id": "f1", "description": "implement flush batch",
             "cause": "", "fix": "add flush logic",
             "functions": fn, "files": [], "status": "active", "tags": []},
            {"id": "f2", "description": "rollback flush batch",
             "cause": "", "fix": "removed flush logic",
             "functions": fn, "files": [], "status": "active", "tags": []},
            {"id": "f3", "description": "re-enable flush batch",
             "cause": "", "fix": "enable flush again",
             "functions": fn, "files": [], "status": "active", "tags": []},
        ]
        new_entry = {"id": "f4", "description": "undo flush batch",
                     "cause": "", "fix": "deleted flush",
                     "functions": fn, "files": [], "status": "active", "tags": []}
        detector = RevertDetector(engine)
        result = detector.check(new_entry, entries + [new_entry])
        assert result is not None
        for eid in ["f1", "f2", "f3"]:
            assert eid in result.related_entry_ids

    def test_unstable_tag_applied_to_related(self, tmp_path):
        """After check(), related entries in real memory get 'unstable' tag."""
        engine = _make_engine(str(tmp_path))
        fn = ["OnBarBoundary"]

        r1 = _add(engine, "add live render feature",
                  fix="implement OnBarBoundary live render",
                  functions=fn)
        r2 = _add(engine, "revert live render feature",
                  fix="remove OnBarBoundary live render",
                  functions=fn)
        r3 = _add(engine, "add live render v2",
                  fix="enable OnBarBoundary live render again",
                  functions=fn)
        r4 = _add(engine, "revert live render v2",
                  fix="disable OnBarBoundary live render v2",
                  functions=fn)

        # r4 result should carry a revert_warning
        assert r4.get("revert_warning") is not None
        warning = r4["revert_warning"]
        assert warning["revert_count"] >= UNSTABLE_THRESHOLD

        # All entries should have 'unstable' tag in storage
        mem = {e["id"]: e for e in engine._read_memory()}
        for res in [r1, r2, r3, r4]:
            eid = res["entry"]["id"]
            assert "unstable" in mem[eid]["tags"], \
                "Expected 'unstable' tag on entry {}".format(eid)

    def test_warning_message_contains_surface(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        fn = ["myspecialfunc"]
        entries = [
            {"id": "g1", "description": "implement myspecialfunc",
             "cause": "", "fix": "add myspecialfunc",
             "functions": fn, "files": [], "status": "active", "tags": []},
            {"id": "g2", "description": "undo myspecialfunc",
             "cause": "", "fix": "removed myspecialfunc",
             "functions": fn, "files": [], "status": "active", "tags": []},
            {"id": "g3", "description": "re-add myspecialfunc",
             "cause": "", "fix": "create myspecialfunc v2",
             "functions": fn, "files": [], "status": "active", "tags": []},
        ]
        new_entry = {"id": "g4", "description": "revert myspecialfunc v2",
                     "cause": "", "fix": "delete myspecialfunc v2",
                     "functions": fn, "files": [], "status": "active", "tags": []}
        detector = RevertDetector(engine)
        result = detector.check(new_entry, entries + [new_entry])
        assert result is not None
        assert "myspecialfunc" in result.message
        assert "WARNING" in result.message


class TestPrecisionGuards:
    """Regression guards for the precision fix (function surface + hotspot)."""

    def test_same_file_different_functions_no_warning(self, tmp_path):
        """Add/revert keywords on a SHARED FILE but DIFFERENT functions must not
        warn — this was the dominant false-positive on real data (hot files)."""
        engine = _make_engine(str(tmp_path))
        f = ["ScoreNoteInserter.cpp"]
        entries = [
            {"id": "p1", "description": "add synchronous paint", "cause": "",
             "fix": "", "functions": ["PaintNotes"], "files": f,
             "status": "active", "tags": []},
            {"id": "p2", "description": "remove redundant call", "cause": "",
             "fix": "", "functions": ["LinkNotes"], "files": f,
             "status": "active", "tags": []},
            {"id": "p3", "description": "add bar boundary handling", "cause": "",
             "fix": "", "functions": ["OnBarBoundary"], "files": f,
             "status": "active", "tags": []},
        ]
        new_entry = {"id": "p4", "description": "revert grace note tweak",
                     "cause": "", "fix": "", "functions": ["CompleteNote"],
                     "files": f, "status": "active", "tags": []}
        result = RevertDetector(engine).check(new_entry, entries + [new_entry])
        assert result is None

    def test_revert_word_only_in_fix_no_warning(self, tmp_path):
        """Revert/add words appearing ONLY in the fix mechanism (not the intent
        description) must not classify an entry as churn."""
        engine = _make_engine(str(tmp_path))
        fn = ["SharedFunc"]
        entries = [
            {"id": "q1", "description": "improve quantization accuracy",
             "cause": "", "fix": "added a clamp and removed the old branch",
             "functions": fn, "files": [], "status": "active", "tags": []},
            {"id": "q2", "description": "tune phase offset",
             "cause": "", "fix": "removed dead code, added guard",
             "functions": fn, "files": [], "status": "active", "tags": []},
        ]
        new_entry = {"id": "q3", "description": "adjust note linking",
                     "cause": "", "fix": "removed redundant call; added log",
                     "functions": fn, "files": [], "status": "active", "tags": []}
        # None of the descriptions carry add/revert intent → no classification
        result = RevertDetector(engine).check(new_entry, entries + [new_entry])
        assert result is None

    def test_apply_tags_false_has_no_side_effect(self, tmp_path):
        """check(apply_tags=False) must never mutate the store."""
        engine = _make_engine(str(tmp_path))
        fn = ["ChurnFunc"]
        for desc, fx in [("add churn", "implement"), ("revert churn", "remove"),
                         ("re-add churn", "enable"), ("revert churn v2", "disable")]:
            engine.add_memory({"type": "bug_fix", "description": desc, "cause": "",
                               "fix": fx, "files": [], "functions": fn,
                               "confidence": 0.9, "tags": []})
        before = json.dumps(engine._read_memory(), sort_keys=True)
        new = {"id": "zz", "description": "revert churn v3", "cause": "",
               "fix": "disable", "functions": fn, "files": [], "status": "active",
               "tags": []}
        RevertDetector(engine).check(new, engine._read_memory() + [new], apply_tags=False)
        after = json.dumps(engine._read_memory(), sort_keys=True)
        assert before == after


class TestFileBasedDetection:
    def test_file_surface_triggers_warning(self, tmp_path):
        """Detection also works on shared files when no functions overlap."""
        engine = _make_engine(str(tmp_path))
        f = ["ScoreNoteInserter.cpp"]
        entries = [
            {"id": "h1", "description": "add provisional render",
             "cause": "", "fix": "implement render",
             "functions": [], "files": f, "status": "active", "tags": []},
            {"id": "h2", "description": "revert provisional render",
             "cause": "", "fix": "remove render",
             "functions": [], "files": f, "status": "active", "tags": []},
            {"id": "h3", "description": "add provisional render v2",
             "cause": "", "fix": "enable render v2",
             "functions": [], "files": f, "status": "active", "tags": []},
        ]
        new_entry = {"id": "h4", "description": "revert render v2",
                     "cause": "", "fix": "disabled render v2",
                     "functions": [], "files": f, "status": "active", "tags": []}
        detector = RevertDetector(engine)
        result = detector.check(new_entry, entries + [new_entry])
        assert result is not None
        assert "scorenoteinserter.cpp" in result.unstable_files


# ---------------------------------------------------------------------------
# Integration: add_memory return value
# ---------------------------------------------------------------------------

class TestAddMemoryIntegration:
    def test_no_warning_in_normal_add(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        result = _add(engine, "Fix bar quantization")
        assert result["revert_warning"] is None

    def test_warning_returned_by_add_memory(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        fn = ["SyncBarToCurrentTime"]
        _add(engine, "implement SyncBarToCurrentTime batch",
             fix="add sync logic", functions=fn)
        _add(engine, "revert SyncBarToCurrentTime batch",
             fix="removed sync logic", functions=fn)
        _add(engine, "re-implement SyncBarToCurrentTime batch",
             fix="enable sync again", functions=fn)
        result = _add(engine, "revert SyncBarToCurrentTime batch v2",
                      fix="disable sync v2", functions=fn)

        assert result["revert_warning"] is not None
        w = result["revert_warning"]
        assert "synctobarcurrenttime" in [f.lower() for f in w["unstable_functions"]] or \
               any("sync" in f.lower() for f in w["unstable_functions"])
        assert w["revert_count"] >= UNSTABLE_THRESHOLD
        assert "WARNING" in w["message"]

    def test_add_memory_still_returns_entry_and_conflicts(self, tmp_path):
        """revert_warning field must not break existing return structure."""
        engine = _make_engine(str(tmp_path))
        result = _add(engine, "Normal memory entry with no pattern")
        assert "entry" in result
        assert "conflicts" in result
        assert "revert_warning" in result


# ---------------------------------------------------------------------------
# CLI: verify run.py still works (revert_warning is extra field, not breaking)
# ---------------------------------------------------------------------------

class TestCLICompat:
    def test_add_memory_cli_succeeds(self, tmp_path):
        import subprocess
        result = subprocess.run(
            ["c:/python313/python.exe",
             "D:/ai_memory_system/run.py",
             "--project", str(tmp_path),
             "add_memory",
             "--type", "note",
             "--description", "cli compat test for revert detection",
             "--confidence", "0.8"],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert "entry" in data
        assert "revert_warning" in data
