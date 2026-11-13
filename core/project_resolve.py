"""Which project store does a working directory belong to?

One implementation for the MCP server and the CLI/slash commands.

History (audit #4): three different resolvers disagreed, and each had a way to
drop knowledge into a shared bucket where it was never injected again -
* the CLI walked up to the drive root, whose empty name slugified to
  "default", so every unconnected folder on that drive shared one store;
* the MCP server fell back to the root store when a folder had no store yet;
* the global Visual Studio config pinned whichever project connected last.

Now: a registered project that contains the directory wins; otherwise the git
root (or, for explicit CLI use, the directory itself) gets its own store, which
is registered on first use. Home, drive roots and generic folders never become
projects.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "connected_projects.json"
PROJECTS = ROOT / "data" / "projects"

_SHARED_NAMES = {"", "default", "desktop", "documents", "downloads", "system32",
                 "windows", "temp", "tmp", "users", "home", "ide", "common7"}


def slugify(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def _registry() -> dict:
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def register(slug: str, path: Path, auto: bool = True) -> None:
    reg = _registry()
    if slug in reg and (reg[slug] or {}).get("path"):
        return
    reg[slug] = {"path": str(path), "last_connected": datetime.now(timezone.utc).isoformat()}
    if auto:
        reg[slug]["auto"] = True
    try:
        REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass


def slug_for_path(path: Path) -> Optional[str]:
    """Registered project containing ``path`` (the most specific one)."""
    try:
        p = str(path.resolve()).lower()
    except OSError:
        return None
    best = None
    for slug, info in _registry().items():
        raw = (info or {}).get("path") or ""
        if not raw:
            continue
        root = str(Path(raw)).lower().rstrip("\\/")
        if p == root or p.startswith(root + os.sep):
            if best is None or len(root) > best[1]:
                best = (slug, len(root))
    return best[0] if best else None


def git_root(path: Path) -> Optional[Path]:
    for d in [path] + list(path.parents)[:6]:
        if (d / ".git").exists():
            return d
    return None


def resolve(cwd: Path, allow_plain_dir: bool = False) -> Optional[str]:
    """Slug of the store for ``cwd``; None means "the shared root store".

    allow_plain_dir: let a folder that is not a git repository become its own
    project. True for explicit user commands run inside that folder, False for
    an IDE-launched server whose working directory may be anything.
    """
    slug = slug_for_path(cwd)
    if slug:
        return slug
    root = git_root(cwd)
    if root is None:
        own = slugify(cwd.name)
        if own and own not in _SHARED_NAMES:
            from .storage import has_store
            if has_store(PROJECTS / own):
                return own
        if not allow_plain_dir:
            return None
        root = cwd
    try:
        home = Path(os.path.expanduser("~")).resolve()
        if root.resolve() == home or root.parent == root:
            return None
    except OSError:
        return None
    slug = slugify(root.name)
    if slug in _SHARED_NAMES:
        return None
    register(slug, root)
    return slug
