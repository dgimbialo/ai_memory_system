"""SessionEnd hook - write the hand-off digest for the next session.

Collects what was recorded since the previous session summary and stores one
"session-summary" note (shown as "Last session" at the next SessionStart).
Nothing is written when fewer than two entries were recorded.

Exit code 0 always; prints nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    project = None
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--project" and i + 1 < len(argv):
            project = argv[i + 1]
    try:
        from core.storage import has_store
        slug = project.strip().lower().replace(" ", "_") if project else None
        data_dir = ROOT / "data" / "projects" / slug if slug else ROOT / "data"
        if not has_store(data_dir):
            return
        from core import embeddings
        embeddings.use_daemon(True)  # no model load in a hook if the daemon is up
        from core.engine import MemoryEngine
        MemoryEngine(data_dir).auto_session_summary()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
