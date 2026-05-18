"""Tests for Semantic Deduplication."""
import sys
import os
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.engine import MemoryEngine
from core.deduplicator import (
    Deduplicator, DEDUP_THRESHOLD,
    _build_clusters, _pick_canonical, _entry_text, _intra_similarity,
)
from core.embeddings import embed, cosine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(tmp_path: str) -> MemoryEngine:
    return MemoryEngine(tmp_path)


_CTR = [0]


def _add(engine: MemoryEngine,
         description: str,
         fix: str = "",
         decisions: list = None,
         type_: str = "bug_fix",
         files: list = None,
         functions: list = None,
         confidence: float = 0.9) -> str:
    _CTR[0] += 1
    uid = _CTR[0]
    r = engine.add_memory({
        "type": type_,
        "description": description,
        "cause": "cause_{}".format(uid),
        "fix": fix or "fix_{}".format(uid),
        "files": files or ["file_{}.cpp".format(uid)],
        "functions": functions or ["Func_{}".format(uid)],
        "decisions": decisions or [],
        "confidence": confidence,
        "tags": ["test"],
    })
    return r["entry"]["id"]


def _near_duplicate_pair(engine: MemoryEngine) -> tuple:
    """Add two entries whose texts are near-identical (should cluster together)."""
    text = (
        "Grace group is atomic: all grace notes plus melody must be in the same bar. "
        "Walking backwards from the last CNoteEx collects all consecutive GraceNote children. "
        "The full embellishment pattern must be available to BarPostProcessor."
    )
    id_a = _add(engine, text, fix=text, decisions=[text], type_="decision")
    # Minimal variation: change one word
    text_b = text.replace("Walking backwards", "Iterating backwards")
    id_b = _add(engine, text_b, fix=text_b, decisions=[text_b], type_="decision")
    return id_a, id_b


# ---------------------------------------------------------------------------
# Unit: _entry_text
# ---------------------------------------------------------------------------

class TestEntryText:
    def test_combines_fields(self):
        e = {"description": "foo", "fix": "bar", "decisions": ["baz"]}
        t = _entry_text(e)
        assert "foo" in t
        assert "bar" in t
        assert "baz" in t

    def test_empty_entry(self):
        assert _entry_text({}) == ""


# ---------------------------------------------------------------------------
# Unit: _pick_canonical
# ---------------------------------------------------------------------------

class TestPickCanonical:
    def test_picks_highest_confidence(self):
        cluster = [
            {"id": "a", "confidence": 0.7, "decisions": [], "timestamp": "2026-01-01"},
            {"id": "b", "confidence": 0.95, "decisions": [], "timestamp": "2026-01-02"},
            {"id": "c", "confidence": 0.8, "decisions": [], "timestamp": "2026-01-03"},
        ]
        canon = _pick_canonical(cluster)
        assert canon["id"] == "b"

    def test_tie_broken_by_decisions_count(self):
        cluster = [
            {"id": "a", "confidence": 0.9, "decisions": ["d1"], "timestamp": "2026-01-01"},
            {"id": "b", "confidence": 0.9, "decisions": ["d1", "d2"], "timestamp": "2026-01-02"},
        ]
        canon = _pick_canonical(cluster)
        assert canon["id"] == "b"


# ---------------------------------------------------------------------------
# Unit: _build_clusters � uses real embeddings (hash fallback)
# ---------------------------------------------------------------------------

class TestBuildClusters:
    def test_identical_texts_cluster(self):
        text = "fix bar quantization using proportional scaling"
        e1 = {"id": "x1", "type": "bug_fix",
              "description": text, "fix": "", "decisions": [], "status": "active"}
        e2 = {"id": "x2", "type": "bug_fix",
              "description": text, "fix": "", "decisions": [], "status": "active"}
        e3 = {"id": "x3", "type": "bug_fix",
              "description": "completely unrelated topic drone filter",
              "fix": "", "decisions": [], "status": "active"}
        entries = [e1, e2, e3]
        embs = [embed(_entry_text(e)) for e in entries]
        clusters = _build_clusters(entries, embs, threshold=0.88)
        assert len(clusters) >= 1
        clustered_ids = {e["id"] for cl in clusters for e in cl}
        assert "x1" in clustered_ids
        assert "x2" in clustered_ids

    def test_different_types_not_clustered(self):
        text = "fix bar quantization using proportional scaling"
        e1 = {"id": "y1", "type": "bug_fix",
              "description": text, "fix": "", "decisions": [], "status": "active"}
        e2 = {"id": "y2", "type": "feature",
              "description": text, "fix": "", "decisions": [], "status": "active"}
        entries = [e1, e2]
        embs = [embed(_entry_text(e)) for e in entries]
        clusters = _build_clusters(entries, embs, threshold=0.88)
        assert len(clusters) == 0

    def test_no_clusters_below_threshold(self):
        e1 = {"id": "z1", "type": "bug_fix",
              "description": "grace note bar placement quantization",
              "fix": "", "decisions": [], "status": "active"}
        e2 = {"id": "z2", "type": "bug_fix",
              "description": "drone MIDI channel filter NoteOn tracking",
              "fix": "", "decisions": [], "status": "active"}
        entries = [e1, e2]
        embs = [embed(_entry_text(e)) for e in entries]
        clusters = _build_clusters(entries, embs, threshold=0.88)
        assert len(clusters) == 0


# ---------------------------------------------------------------------------
# Integration: Deduplicator with real engine
# ---------------------------------------------------------------------------

class TestDeduplicatorFindClusters:
    def test_no_duplicates_returns_empty(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        _add(engine, "Grace note placement at bar boundary rule")
        _add(engine, "MIDI drone channel filter NoteOn NoteOff tracking")
        dedup = Deduplicator(engine, threshold=0.88)
        assert dedup.find_clusters() == []

    def test_identical_entries_form_cluster(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        text = "proportional fit scale equals barTicks divided by rawTotal last note fill"
        _add(engine, text, fix=text, type_="decision", decisions=[text])
        _add(engine, text, fix=text, type_="decision", decisions=[text])
        dedup = Deduplicator(engine, threshold=0.88)
        clusters = dedup.find_clusters()
        assert len(clusters) >= 1
        assert len(clusters[0]) >= 2


class TestDeduplicatorApplyDryRun:
    def test_dry_run_no_write(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        text = "bar quantization proportional scaling grace note atomic group"
        id_a = _add(engine, text, fix=text, type_="decision")
        id_b = _add(engine, text, fix=text, type_="decision")
        result = Deduplicator(engine, threshold=0.88).apply(dry_run=True)
        assert result["dry_run"] is True
        assert result["clusters_found"] >= 1
        assert result["merged_count"] == 0
        # Originals must be untouched
        mem = {e["id"]: e for e in engine._read_memory()}
        assert mem[id_a]["status"] != "superseded"
        assert mem[id_b]["status"] != "superseded"

    def test_dry_run_returns_cluster_details(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        text = "phase offset detection halfBeat threshold syncopation guard"
        _add(engine, text, fix=text, type_="decision")
        _add(engine, text, fix=text, type_="decision")
        result = Deduplicator(engine, threshold=0.88).apply(dry_run=True)
        details = result["cluster_details"]
        assert len(details) >= 1
        d = details[0]
        assert "canonical_id" in d
        assert "member_ids" in d
        assert "similarities" in d


class TestDeduplicatorApplyWrite:
    def test_apply_supersedes_non_canonical(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        text = "grace group atomic all grace notes melody same bar BarPostProcessor"
        id_a = _add(engine, text, fix=text, type_="decision",
                    decisions=["Grace group atomicity rule A"])
        id_b = _add(engine, text, fix=text, type_="decision",
                    decisions=["Grace group atomicity rule B"])
        result = Deduplicator(engine, threshold=0.88).apply(dry_run=False)
        assert result["merged_count"] >= 1
        # All original members are superseded (including canonical)
        assert result["superseded_count"] >= 2
        mem = {e["id"]: e for e in engine._read_memory()}
        # Both originals superseded
        assert mem[id_a]["status"] == "superseded"
        assert mem[id_b]["status"] == "superseded"

    def test_merged_entry_has_union_decisions(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        text = "proportional bar fill scale last note quantization tick space"
        id_a = _add(engine, text, fix=text, type_="decision",
                    decisions=["Decision from entry A"])
        id_b = _add(engine, text, fix=text, type_="decision",
                    decisions=["Decision from entry B"])
        Deduplicator(engine, threshold=0.88).apply(dry_run=False)
        mem = engine._read_memory()
        merged = [e for e in mem if "deduplicated" in (e.get("tags") or [])]
        assert len(merged) >= 1
        all_decisions = merged[0].get("decisions") or []
        assert any("Decision from entry A" in d or "Decision from entry B" in d
                   for d in all_decisions)

    def test_merged_entry_tagged_merged_and_deduplicated(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        text = "cross bar tail grace note phase routing boundary detection"
        _add(engine, text, fix=text, type_="decision")
        _add(engine, text, fix=text, type_="decision")
        Deduplicator(engine, threshold=0.88).apply(dry_run=False)
        mem = engine._read_memory()
        merged = [e for e in mem if "deduplicated" in (e.get("tags") or [])]
        assert len(merged) >= 1
        assert "merged" in merged[0].get("tags", [])

    def test_apply_logs_action(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        text = "tick space quantization beatTicks per bar constant PPQN"
        _add(engine, text, fix=text, type_="decision")
        _add(engine, text, fix=text, type_="decision")
        Deduplicator(engine, threshold=0.88).apply(dry_run=False)
        log = engine.storage.read("activity_log.json", default=[])
        dedup_logs = [l for l in log if l.get("action") == "deduplicate"]
        assert len(dedup_logs) >= 1

    def test_no_clusters_returns_early(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        _add(engine, "Grace note placement boundary rule")
        _add(engine, "MIDI drone tone NoteOff tracking channel")
        result = Deduplicator(engine, threshold=0.88).apply(dry_run=False)
        assert result["clusters_found"] == 0
        assert result["merged_count"] == 0

    def test_three_member_cluster(self, tmp_path):
        engine = _make_engine(str(tmp_path))
        text = "bar quantization proportional fit scale rawTotal ticks PPQN"
        _add(engine, text, fix=text, type_="decision", decisions=["D1"])
        _add(engine, text, fix=text, type_="decision", decisions=["D2"])
        _add(engine, text, fix=text, type_="decision", decisions=["D3"])
        result = Deduplicator(engine, threshold=0.88).apply(dry_run=False)
        assert result["superseded_count"] >= 2
        mem = engine._read_memory()
        merged = [e for e in mem if "deduplicated" in (e.get("tags") or [])]
        assert len(merged) >= 1
        # All 3 decisions present in merged entry
        decisions = merged[0].get("decisions") or []
        assert len(decisions) >= 3

    def test_superseded_not_reduplicated(self, tmp_path):
        """After dedup, running again finds no new clusters."""
        engine = _make_engine(str(tmp_path))
        text = "phase adjusted bar end calculation halfBeat syncopation guard"
        _add(engine, text, fix=text, type_="decision")
        _add(engine, text, fix=text, type_="decision")
        Deduplicator(engine, threshold=0.88).apply(dry_run=False)
        # Second run
        result2 = Deduplicator(engine, threshold=0.88).apply(dry_run=False)
        assert result2["clusters_found"] == 0


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

class TestDeduplicateCLI:
    def test_find_duplicates_no_dupes(self, tmp_path):
        import subprocess
        engine = _make_engine(str(tmp_path))
        _add(engine, "Grace note bar placement rule unique alpha")
        _add(engine, "Drone MIDI channel filter beta unique")

        result = subprocess.run(
            ["c:/python313/python.exe",
             "D:/ai_memory_system/run.py",
             "--project", str(tmp_path),
             "find_duplicates"],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["clusters_found"] == 0

    def test_deduplicate_dry_run_cli(self, tmp_path):
        import subprocess
        engine = _make_engine(str(tmp_path))
        text = "proportional bar fill quantization rawTotal scale ticks constant"
        _add(engine, text, fix=text, type_="decision")
        _add(engine, text, fix=text, type_="decision")

        result = subprocess.run(
            ["c:/python313/python.exe",
             "D:/ai_memory_system/run.py",
             "--project", str(tmp_path),
             "deduplicate", "--dry-run"],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["dry_run"] is True
        assert data["clusters_found"] >= 1
        assert data["merged_count"] == 0

    def test_deduplicate_apply_cli(self, tmp_path):
        import subprocess
        engine = _make_engine(str(tmp_path))
        text = "grace group atomic bar boundary grace melody same bar walk backwards"
        _add(engine, text, fix=text, type_="decision")
        _add(engine, text, fix=text, type_="decision")

        result = subprocess.run(
            ["c:/python313/python.exe",
             "D:/ai_memory_system/run.py",
             "--project", str(tmp_path),
             "deduplicate"],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["clusters_found"] >= 1
        assert data["merged_count"] >= 1
