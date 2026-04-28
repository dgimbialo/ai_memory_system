"""Bootstrap script — creates folder structure and initializes JSON DBs.

Idempotent: safe to run multiple times. Will not overwrite existing data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORE_DIR = ROOT / "core"
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = ROOT / "templates"

REQUIRED_DIRS = [CORE_DIR, DATA_DIR, TEMPLATES_DIR]
REQUIRED_CORE_FILES = [
    "engine.py",
    "storage.py",
    "models.py",
    "conflict.py",
    "embeddings.py",
    "updater.py",
    "__init__.py",
    # Markdown projection + lint
    "wiki_md.py",
    "lint.py",
    # VS Code / Copilot infrastructure modules
    "vscode_infra.py",
    "copilot_logger.py",
    "learner.py",
]
INITIAL_DB = {
    "memory.json": [],
    "wiki.json": {"sections": {}, "entry_count": 0},
    "conflicts.json": [],
    "activity_log.json": [],
    # VS Code / Copilot infrastructure data files
    "copilot_activity.json": [],
    "managed_projects.json": [],
    "learn_log.json": [],
}


def ensure_dirs() -> None:
    for d in REQUIRED_DIRS:
        d.mkdir(parents=True, exist_ok=True)


def ensure_core_files() -> list[str]:
    missing = []
    for name in REQUIRED_CORE_FILES:
        if not (CORE_DIR / name).exists():
            missing.append(name)
    return missing


def ensure_data_files() -> None:
    for name, default in INITIAL_DB.items():
        path = DATA_DIR / name
        if not path.exists():
            with path.open("w", encoding="utf-8") as f:
                json.dump(default, f, indent=2)


def main() -> int:
    ensure_dirs()
    missing = ensure_core_files()
    ensure_data_files()

    if missing:
        print("[bootstrap] WARNING: missing core source files:", ", ".join(missing))
        print("[bootstrap] Re-run the project generator to restore them.")
        return 1

    # Initialize engine to validate everything is consistent.
    sys.path.insert(0, str(ROOT))
    from core.engine import MemoryEngine  # noqa: E402

    engine = MemoryEngine(DATA_DIR)
    state = engine.state()
    print("[bootstrap] AI Memory System initialized.")
    print(f"[bootstrap] Data dir     : {DATA_DIR}")
    print(f"[bootstrap] Templates dir: {TEMPLATES_DIR}")
    print(f"[bootstrap] Embedding backend: {state['embedding_backend']}")
    print(f"[bootstrap] Entries: {state['entry_count']} | Conflicts: {state['conflict_count']}")
    print("[bootstrap] Ready.")
    print("[bootstrap]   Memory CLI  : python run.py --help")
    print("[bootstrap]   VS Code CLI : python run_infra.py --help")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
