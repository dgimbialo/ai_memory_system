"""Git context for a project store: where does this project live, and which
branch/commit is checked out right now.

Memories are stamped with branch + commit at write time so recall can tell a
fact learned on an unmerged feature branch from one that is true on master
(audit #4: entries literally said "uncommitted" and "implemented on
dev/taras/redesign_stage_1" in free text, invisible to ranking).
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "connected_projects.json"

_CACHE: Dict[str, Tuple[float, Tuple[str, str]]] = {}
_TTL = 45.0


def project_path(slug: str) -> Optional[Path]:
    """Filesystem path of a connected project, from the connect.py registry."""
    try:
        reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
        p = Path((reg.get(slug) or {}).get("path") or "")
        return p if str(p) and p.exists() else None
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def current(slug: str) -> Tuple[str, str]:
    """(branch, short_commit) of the project's working tree, or ("", "")."""
    now = time.time()
    hit = _CACHE.get(slug)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    result = ("", "")
    path = project_path(slug)
    if path is not None:
        try:
            kwargs: dict = dict(capture_output=True, text=True, timeout=3)
            import os
            if os.name == "nt":
                kwargs["creationflags"] = 0x08000000  # no console window
            r = subprocess.run(["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"], **kwargs)
            branch = r.stdout.strip() if r.returncode == 0 else ""
            commit = ""
            if branch:
                r2 = subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], **kwargs)
                commit = r2.stdout.strip() if r2.returncode == 0 else ""
            result = (branch, commit)
        except (OSError, subprocess.SubprocessError):
            result = ("", "")
    _CACHE[slug] = (now, result)
    return result
