"""Per-prompt recall: pick the few memories worth showing for one user prompt.

Shared by the UserPromptSubmit hook (lexical-only fallback, no model) and the
daemon (hybrid, warm multilingual model).

Selection rules (calibrated on a real 419-entry store, audit #4):
* **strong** - at least two distinct rare query terms found in the entry
  (BM25). Chit-chat never clears this; identifiers and English topics do.
  Shown in full, up to 3.
* **related** - semantic-only neighbours, used when the prompt carries enough
  content (4+ terms) but shares no vocabulary with the store: Ukrainian prompts
  against English entries. e5 cosines do not separate "relevant" from "noise"
  in absolute terms (0.82-0.84 vs 0.79-0.83), so this path is deliberately
  small: at most 2 one-liners.
* Nothing is repeated within a session, and a per-project rate limit stops an
  automated loop from re-reading the same memories all day (one project logged
  2898 recalls in a month, 1599 of them the identical result set).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_STRONG = 3
MAX_RELATED = 3
MIN_MATCHED = 2
# BM25 grows with log(store size) through the idf term, so the floor scales the
# same way: 1.0 x ln(1+N) is ~6.0 on the 419-entry store it was calibrated on
# (relevant prompts scored 10-19, chit-chat matched at most one term).
MIN_BM25_PER_LOG_N = 1.0
MIN_TERMS_FOR_SEMANTIC = 4
MAX_CHARS = 1500
STORM_WINDOW_MIN = 10
STORM_MAX_EVENTS = 15


def _line(e: Dict[str, Any], full: bool, current_branch: str = "") -> List[str]:
    tags = e.get("tags") or []
    marks = ""
    if "unstable" in tags:
        marks += " ⚠UNSTABLE"
    br = e.get("branch") or ""
    if br and current_branch and br != current_branch and br not in ("main", "master"):
        marks += f" [from branch {br}]"
    files = ", ".join(e.get("files") or [])[:60]
    out = [f"- [{(e.get('id') or '?')[:8]}] ({e.get('type')}, conf="
           f"{float(e.get('confidence') or 0):.2f}){marks} "
           f"{(e.get('description') or '')[:150]}" + (f"  → {files}" if files else "")]
    if full:
        fix = (e.get("fix") or "").strip()
        if fix:
            out.append(f"    fix: {fix[:170]}")
    return out


def select(entries: List[Dict[str, Any]], prompt: str, semantic: bool,
           seen: Optional[set] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Return {'strong': [...], 'related': [...]} for the prompt."""
    from .search import rank, tokenize
    seen = seen or set()
    terms = list(dict.fromkeys(tokenize(prompt)))
    if len(terms) < 2:
        return {"strong": [], "related": []}
    live = []
    for e in entries:
        if e.get("status") not in ("active", "conflict"):
            continue
        tags = e.get("tags") or []
        if "archived" in tags or "session-summary" in tags:
            continue
        live.append(e)
    if not live:
        return {"strong": [], "related": []}
    r = rank(live, prompt, semantic=semantic)
    order = sorted(range(len(live)),
                   key=lambda i: (r[i]["relevance"], live[i].get("timestamp") or ""), reverse=True)
    min_bm25 = MIN_BM25_PER_LOG_N * math.log(1 + len(live))
    strong, related = [], []
    for i in order:
        if live[i].get("id") in seen:
            continue
        if r[i]["matched"] >= MIN_MATCHED and r[i]["bm25"] >= min_bm25:
            if len(strong) < MAX_STRONG:
                strong.append(live[i])
    if semantic and len(terms) >= MIN_TERMS_FOR_SEMANTIC and len(strong) < MAX_RELATED:
        picked = {e.get("id") for e in strong}
        by_sem = sorted(range(len(live)), key=lambda i: r[i]["sem"], reverse=True)
        for i in by_sem:
            if len(strong) + len(related) >= MAX_RELATED:
                break
            if r[i]["sem"] <= 0 or live[i].get("id") in seen or live[i].get("id") in picked:
                continue
            related.append(live[i])
    return {"strong": strong, "related": related}


def recall_for_prompt(data_dir: Path, project: Optional[str], prompt: str,
                      session_id: str = "", semantic: bool = False,
                      current_branch: str = "") -> str:
    """Markdown block to append to the prompt context ('' = stay silent)."""
    from .storage import Storage, has_store
    if not prompt or not has_store(data_dir):
        return ""
    st = Storage(data_dir)
    now = datetime.now(timezone.utc)
    since = (now - timedelta(minutes=STORM_WINDOW_MIN)).isoformat()
    if st.count_events("inject_prompt_recall", since) >= STORM_MAX_EVENTS:
        return ""  # automated loop: stop feeding it
    entries = st.read("memory.json", [])
    if not isinstance(entries, list) or not entries:
        return ""
    picked = select(entries, prompt, semantic, st.seen_in_session(session_id))
    strong, related = picked["strong"], picked["related"]
    if not strong and not related:
        return ""
    lines = ["## Relevant project memory (auto-recall for this prompt)"]
    for e in strong:
        lines += _line(e, True, current_branch)
    if related:
        lines.append("Possibly related (semantic match):")
        for e in related:
            lines += _line(e, False, current_branch)
    lines.append("(memory_confirm <id> if one of these proves right, memory_reject <id> if wrong)")
    out = "\n".join(lines)[:MAX_CHARS]
    ids = [e.get("id", "") for e in strong + related]
    try:
        st.mark_seen(session_id, ids)
        st.append_log("activity_log.json", {
            "timestamp": now.isoformat(),
            "action": "inject_prompt_recall",
            "affected": ids,
            "reason": "per-prompt recall: {} strong, {} related ({})".format(
                len(strong), len(related), "hybrid" if semantic else "lexical"),
        })
    except Exception:
        pass
    return out
