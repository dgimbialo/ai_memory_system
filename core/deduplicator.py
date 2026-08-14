"""Semantic deduplication for the AI Memory System.

Finds clusters of entries that are near-duplicates (high embedding
similarity + same type + active status), and merges each cluster into
one canonical entry.

Algorithm
---------
1. Embed all active entries using the existing embeddings module
   (sentence-transformers when available, hash-BOW fallback otherwise).
2. Compute pairwise cosine similarity.
3. Build clusters via single-linkage: two entries are in the same
   cluster if their similarity >= DEDUP_THRESHOLD.
4. For each cluster of size >= 2:
   - canonical entry = the one with highest confidence (tie: oldest id)
   - decisions, files, functions, tags = union of all cluster members
   - confidence = max of cluster
   - supersede all non-canonical members
5. Return a summary of what changed (or would change in dry_run mode).

Threshold
---------
DEDUP_THRESHOLD = 0.88  -- higher than conflict threshold (0.62)
                           to avoid merging merely related entries.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import MemoryEngine

DEDUP_THRESHOLD: float = 0.88

# Statuses eligible for deduplication
_ELIGIBLE_STATUS = {"active", "conflict"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class Deduplicator:
    def __init__(
        self,
        engine: "MemoryEngine",
        threshold: float = DEDUP_THRESHOLD,
    ) -> None:
        self._engine = engine
        self.threshold = threshold

    # ------------------------------------------------------------------
    # preview: read-only, returns clusters without modifying anything
    # ------------------------------------------------------------------

    def find_clusters(self) -> List[List[Dict[str, Any]]]:
        """Return list of duplicate clusters (each cluster is a list of entries).

        Only clusters with >= 2 members are returned.
        Entries must share the same type and have eligible status.
        """
        entries = self._load_eligible()
        if len(entries) < 2:
            return []

        embeddings = self._embed_all(entries)
        return _build_clusters(entries, embeddings, self.threshold)

    # ------------------------------------------------------------------
    # apply: merge clusters, supersede originals
    # ------------------------------------------------------------------

    def apply(
        self,
        dry_run: bool = False,
        require_same_files: bool = False,
    ) -> Dict[str, Any]:
        """Find and merge all duplicate clusters.

        Parameters
        ----------
        dry_run : if True, compute clusters but do NOT write anything.
        require_same_files : only merge clusters whose members all touch the
            exact same file set. Used by the unattended daily auto-dedup —
            textual similarity alone is not enough evidence to merge without
            a human looking on.

        Returns
        -------
        dict with keys:
            clusters_found, merged_count, superseded_count, dry_run, cluster_details
        """
        clusters = self.find_clusters()
        if require_same_files:
            clusters = [
                cl for cl in clusters
                if len({tuple(sorted(e.get("files") or [])) for e in cl}) == 1
            ]
        if not clusters:
            return {
                "clusters_found": 0,
                "merged_count": 0,
                "superseded_count": 0,
                "dry_run": dry_run,
                "cluster_details": [],
            }

        details = []
        total_merged = 0
        total_superseded = 0

        for cluster in clusters:
            if dry_run:
                detail = self._describe_cluster(cluster)
                details.append(detail)
                continue

            new_entry, superseded_ids = self._merge_cluster(cluster)
            total_merged += 1
            total_superseded += len(superseded_ids)
            details.append({
                "merged_entry_id":  new_entry["id"],
                "superseded_ids":   superseded_ids,
                "cluster_size":     len(cluster),
                "description":      new_entry.get("description", "")[:80],
            })

        result = {
            "clusters_found":   len(clusters),
            "merged_count":     total_merged,
            "superseded_count": total_superseded,
            "dry_run":          dry_run,
            "cluster_details":  details,
        }

        if not dry_run and clusters:
            self._engine._log(
                "deduplicate",
                [],
                "merged {} cluster(s), superseded {} entries".format(
                    total_merged, total_superseded
                ),
            )

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_eligible(self) -> List[Dict[str, Any]]:
        return [
            e for e in self._engine._read_memory()
            if e.get("status") in _ELIGIBLE_STATUS
        ]

    def _embed_all(
        self, entries: List[Dict[str, Any]]
    ) -> List[List[float]]:
        from .embeddings import embed
        return [embed(_entry_text(e)) for e in entries]

    def _describe_cluster(
        self, cluster: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        canonical = _pick_canonical(cluster)
        return {
            "canonical_id":   canonical.get("id", ""),
            "cluster_size":   len(cluster),
            "member_ids":     [e.get("id", "") for e in cluster],
            "descriptions":   [(e.get("description") or "")[:80] for e in cluster],
            "similarities":   _intra_similarity(cluster),
        }

    def _merge_cluster(
        self, cluster: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, Any], List[str]]:
        """Merge cluster into one canonical entry, supersede the rest.

        Returns (new_entry_dict, list_of_superseded_ids).
        """
        canonical = _pick_canonical(cluster)
        others = [e for e in cluster if e.get("id") != canonical.get("id")]
        all_original_ids = [e["id"] for e in cluster if "id" in e]
        superseded_ids = [e["id"] for e in others if "id" in e]

        # Supersede ALL original cluster members BEFORE add_memory so that
        # conflict detection does not flag the new entry against any of them.
        # This includes canonical so that after merge only the new entry
        # is active, preventing a second dedup run from re-clustering.
        for sid in all_original_ids:
            self._engine.update_status(
                sid, "superseded",
                reason="superseded by deduplication merge",
            )

        # Build merged payload
        all_members = [canonical] + others

        def _union(key: str) -> List[str]:
            seen: Set[str] = set()
            out: List[str] = []
            for e in all_members:
                for item in (e.get(key) or []):
                    norm = str(item).strip()
                    if norm and norm not in seen:
                        seen.add(norm)
                        out.append(norm)
            return out

        tags = _union("tags")
        if "merged" not in tags:
            tags.append("merged")
        if "deduplicated" not in tags:
            tags.append("deduplicated")

        cause = canonical.get("cause") or ""
        cause_suffix = "Deduplicated from {} entries".format(len(cluster))
        cause = (cause + " | " + cause_suffix) if cause else cause_suffix

        payload: Dict[str, Any] = {
            "type":        canonical.get("type", "note"),
            "description": canonical.get("description", ""),
            "cause":       cause,
            "fix":         canonical.get("fix", ""),
            "files":       _union("files"),
            "functions":   _union("functions"),
            "decisions":   _union("decisions"),
            "status":      "active",
            "confidence":  max(float(e.get("confidence") or 0) for e in all_members),
            "tags":        tags,
        }

        result = self._engine.add_memory(payload)
        return result["entry"], all_original_ids


# ---------------------------------------------------------------------------
# Clustering helpers
# ---------------------------------------------------------------------------

def _build_clusters(
    entries: List[Dict[str, Any]],
    embeddings: List[List[float]],
    threshold: float,
) -> List[List[Dict[str, Any]]]:
    """Single-linkage clustering by similarity + same type."""
    n = len(entries)
    from .embeddings import cosine

    # Union-Find
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    for i in range(n):
        for j in range(i + 1, n):
            if entries[i].get("type") != entries[j].get("type"):
                continue
            sim = cosine(embeddings[i], embeddings[j])
            if sim >= threshold:
                union(i, j)

    # Group by root
    from collections import defaultdict
    groups: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    return [
        [entries[i] for i in members]
        for members in groups.values()
        if len(members) >= 2
    ]


def _pick_canonical(cluster: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Choose the entry with highest confidence (tie: most decisions, then oldest)."""
    return max(
        cluster,
        key=lambda e: (
            float(e.get("confidence") or 0),
            len(e.get("decisions") or []),
            # Prefer older entries (lower timestamp string = earlier)
            -(ord(e.get("timestamp", "z")[0]) if e.get("timestamp") else 0),
        ),
    )


def _entry_text(entry: Dict[str, Any]) -> str:
    """Combine entry fields into a single string for embedding."""
    decisions_text = " ".join(entry.get("decisions") or [])
    parts = [
        entry.get("description") or "",
        entry.get("fix") or "",
        decisions_text,
    ]
    return " ".join(p for p in parts if p)


def _intra_similarity(cluster: List[Dict[str, Any]]) -> List[float]:
    """Return pairwise similarities within a cluster (for dry-run preview)."""
    from .embeddings import embed, cosine
    embeddings = [embed(_entry_text(e)) for e in cluster]
    sims = []
    n = len(cluster)
    for i in range(n):
        for j in range(i + 1, n):
            sims.append(round(cosine(embeddings[i], embeddings[j]), 4))
    return sims
