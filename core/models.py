"""Structured memory data models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

VALID_TYPES = {"bug_fix", "feature", "note", "decision"}
VALID_STATUS = {"active", "resolved", "conflict"}


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
    status: str = "active"
    confidence: float = 0.5
    timestamp: str = field(default_factory=_utcnow)
    conflicts_with: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        allowed = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in allowed}
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
