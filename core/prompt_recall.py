"""UserPromptSubmit hook — per-prompt memory recall.

Claude Code runs this on EVERY user prompt, so it must be fast: no engine
import, no embedding model — a weighted keyword-overlap scorer over the raw
memory.json. Session-start injection gives the agent one snapshot; this hook
closes the *pull* half of the read loop by surfacing the 1-3 entries most
relevant to what the user just asked (audit #3: 94% of all reads were the
session-start injection, real per-task recall almost never happened).

Input  (stdin): Claude Code hook JSON — {"prompt": "...", "cwd": ..., ...}
Output (stdout): plain markdown appended to the prompt context; empty when
                 nothing clears the relevance threshold.
Exit code 0 always — never block the prompt.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

MAX_RESULTS = 3
MAX_CHARS = 1400
# Minimum weighted score and distinct matched tokens before we say anything —
# silence is far better than noise on every prompt.
MIN_SCORE = 5
MIN_DISTINCT = 2

_STOP = frozenset((
    "the and for with that this from into are was were will would should could "
    "can not you your our all any has have had been being does did doing when "
    "where what which while about after before because between during under "
    "over then than them they there here how why yes okay please thanks thank "
    "sure hello continue але або щоб для при цьому так добре давай зроби "
    "його вона вони має бути було щоби який яка яке більш дуже також").split())

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}|[Ѐ-ӿ]{3,}")


def _tokens(text: str) -> set:
    return {w.lower() for w in _WORD_RE.findall(text or "") if w.lower() not in _STOP}


def _score(entry: dict, q: set) -> tuple:
    """Weighted overlap: symbols (functions/file basenames) count most."""
    funcs = {f.lower() for f in entry.get("functions") or []}
    files = set()
    for f in entry.get("files") or []:
        base = f.replace("\\", "/").rsplit("/", 1)[-1]
        files.add(base.lower())
        stem = base.rsplit(".", 1)[0].lower()
        if len(stem) >= 3:
            files.add(stem)
    desc = _tokens(entry.get("description") or "")
    body = _tokens((entry.get("cause") or "") + " " + (entry.get("fix") or "")) | \
        {t.lower() for t in entry.get("tags") or []}

    sym_hits = q & (funcs | files)
    desc_hits = (q & desc) - sym_hits
    body_hits = (q & body) - sym_hits - desc_hits
    score = 3 * len(sym_hits) + 2 * len(desc_hits) + 1 * len(body_hits)
    distinct = len(sym_hits | desc_hits | body_hits)
    return score, distinct


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
    q = _tokens(prompt)
    if len(q) < MIN_DISTINCT:
        sys.exit(0)  # too little signal ("continue", "yes", …) — stay silent

    data_dir = (DATA_DIR / "projects" / project.strip().lower().replace(" ", "_")
                if project else DATA_DIR)
    try:
        entries = json.loads((data_dir / "memory.json").read_text(encoding="utf-8"))
    except Exception:
        sys.exit(0)
    if not isinstance(entries, list):
        sys.exit(0)

    scored = []
    for e in entries:
        if e.get("status") not in ("active", "conflict"):
            continue
        tags = e.get("tags") or []
        # Archived = retired; session summaries aggregate dozens of files and
        # match everything — both are noise for targeted recall.
        if "archived" in tags or "session-summary" in tags:
            continue
        s, d = _score(e, q)
        if s >= MIN_SCORE and d >= MIN_DISTINCT:
            scored.append((s, e.get("timestamp") or "", e))
    if not scored:
        sys.exit(0)
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)

    lines = ["## Relevant project memory (auto-recall for this prompt)"]
    for s, _, e in scored[:MAX_RESULTS]:
        unstable = " ⚠UNSTABLE" if "unstable" in (e.get("tags") or []) else ""
        files = ", ".join(e.get("files") or [])[:60]
        lines.append(
            f"- [{e.get('id', '?')[:8]}] ({e.get('type')}, conf="
            f"{float(e.get('confidence') or 0):.2f}){unstable} "
            f"{(e.get('description') or '')[:140]}"
            + (f"  → {files}" if files else "")
        )
        fix = (e.get("fix") or "").strip()
        if fix:
            lines.append(f"    fix: {fix[:160]}")
    lines.append("(memory_confirm <id> if one of these proves right)")
    out = "\n".join(lines)
    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS]
    print(out)

    # Count this as a read in the loop metric (best-effort, atomic append).
    try:
        sys.path.insert(0, str(ROOT))
        from core.storage import Storage
        Storage(data_dir).append_log("activity_log.json", {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "inject_prompt_recall",
            "affected": [e.get("id", "") for _, _, e in scored[:MAX_RESULTS]],
            "reason": f"per-prompt recall matched {len(scored)} entr(ies)",
        })
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
