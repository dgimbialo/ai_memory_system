"""UserPromptSubmit hook - per-prompt memory recall.

Claude Code runs this on EVERY user prompt, so the hook itself never loads a
model: it asks the local memory daemon for a hybrid (BM25 + multilingual
embeddings) recall, and when the daemon is not up it answers from the lexical
leg alone and starts the daemon in the background for the next prompt.

Input  (stdin): Claude Code hook JSON - {"prompt": "...", "session_id": ..., "cwd": ...}
Output (stdout): plain markdown appended to the prompt context; empty when
                 nothing is worth showing.
Exit code 0 always - never block the prompt.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data"

# Kept for backward compatibility with callers/tests of the old lexical scorer.
from core.search import tokenize as _tokenize_list  # noqa: E402

MIN_SCORE = 5
MIN_DISTINCT = 2


def _tokens(text: str) -> set:
    return set(_tokenize_list(text))


def _score(entry: dict, q: set) -> tuple:
    """Legacy weighted-overlap score (symbols x3, description x2, body x1)."""
    funcs = set()
    for f in entry.get("functions") or []:
        funcs |= _tokens(str(f))
    files = set()
    for f in entry.get("files") or []:
        base = str(f).replace("\\", "/").rsplit("/", 1)[-1]
        files |= _tokens(base.rsplit(".", 1)[0])
    desc = _tokens(entry.get("description") or "")
    body = _tokens((entry.get("cause") or "") + " " + (entry.get("fix") or "")) | \
        {t.lower() for t in entry.get("tags") or []}
    sym_hits = q & (funcs | files)
    desc_hits = (q & desc) - sym_hits
    body_hits = (q & body) - sym_hits - desc_hits
    return (3 * len(sym_hits) + 2 * len(desc_hits) + len(body_hits),
            len(sym_hits | desc_hits | body_hits))


def main() -> None:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    project = None
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--project" and i + 1 < len(argv):
            project = argv[i + 1]

    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, OSError):
        sys.exit(0)
    prompt = str(data.get("prompt") or "")
    session_id = str(data.get("session_id") or "")
    if not prompt.strip() or prompt.lstrip().startswith("/"):
        sys.exit(0)  # slash commands carry no topic of their own

    text = None
    try:
        from core import daemon_client
        text = daemon_client.recall(project, prompt, session_id)
    except Exception:
        text = None
    if text is None:  # daemon unavailable: lexical leg only, no model load
        try:
            from core.recall import recall_for_prompt
            slug = project.strip().lower().replace(" ", "_") if project else None
            data_dir = DATA_DIR / "projects" / slug if slug else DATA_DIR
            text = recall_for_prompt(data_dir, project, prompt, session_id, semantic=False)
        except Exception:
            text = ""
    if text:
        print(text)
    sys.exit(0)


if __name__ == "__main__":
    main()
