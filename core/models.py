"""Structured memory data models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

VALID_TYPES = {"bug_fix", "feature", "note", "decision"}
VALID_STATUS = {"active", "resolved", "conflict", "superseded"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class MemoryEntry:
    id: str = field(default_factory=_new_id)
    type: str = "note"
    description: str = ""
    cause: str = ""
    fix: str = ""
    files: List[str] = field(default_factory=list)
    # New: specific functions / methods changed (more granular than files)
    functions: List[str] = field(default_factory=list)
    # New: key architectural decisions with rationale (why, not just what)
    decisions: List[str] = field(default_factory=list)
    status: str = "active"
    confidence: float = 0.5
    timestamp: str = field(default_factory=_utcnow)
    conflicts_with: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    # Dependency graph
    # depends_on: IDs of entries this entry builds upon (e.g. a bug_fix depends on a decision)
    depends_on: List[str] = field(default_factory=list)
    # required_by: IDs of entries that depend on this one (auto-populated on link creation)
    required_by: List[str] = field(default_factory=list)
    # Test-ID traceability
    # test_ids: names of tests that verify this entry's behaviour
    test_ids: List[str] = field(default_factory=list)
    # Reinforcement signal: how many times this memory has been confirmed/reused,
    # and when it was last reused. last_used also resets the decay clock.
    usage_count: int = 0
    last_used: str = ""
    # Immutable decay baseline: decay always recomputes from this value, never
    # from the already-decayed confidence (prevents compounding). 0.0 = unset;
    # reinforce/weaken/update_confidence establish a new baseline.
    confidence_base: float = 0.0

    def validate(self) -> None:
        if self.type not in VALID_TYPES:
            raise ValueError(f"Invalid type '{self.type}'. Must be one of {VALID_TYPES}")
        if self.status not in VALID_STATUS:
            raise ValueError(f"Invalid status '{self.status}'. Must be one of {VALID_STATUS}")
        if not isinstance(self.confidence, (int, float)) or not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError("confidence must be a float between 0.0 and 1.0")
        if not self.description or not isinstance(self.description, str):
            raise ValueError("description is required")
        if not isinstance(self.files, list):
            raise ValueError("files must be a list")
        if not isinstance(self.functions, list):
            raise ValueError("functions must be a list")
        if not isinstance(self.decisions, list):
            raise ValueError("decisions must be a list")
        if not isinstance(self.depends_on, list):
            raise ValueError("depends_on must be a list")
        if not isinstance(self.required_by, list):
            raise ValueError("required_by must be a list")
        if not isinstance(self.test_ids, list):
            raise ValueError("test_ids must be a list")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        allowed = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in allowed}
        # Back-compat: entries saved before functions/decisions/graph fields existed
        clean.setdefault("functions", [])
        clean.setdefault("decisions", [])
        clean.setdefault("depends_on", [])
        clean.setdefault("required_by", [])
        clean.setdefault("test_ids", [])
        clean.setdefault("usage_count", 0)
        clean.setdefault("last_used", "")
        return cls(**clean)


@dataclass
class ConflictRecord:
    id: str = field(default_factory=_new_id)
    entry_a: str = ""
    entry_b: str = ""
    reason: str = ""
    similarity: float = 0.0
    timestamp: str = field(default_factory=_utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WikiSection:
    title: str
    entries: List[str] = field(default_factory=list)  # entry IDs
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
