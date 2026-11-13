"""Hybrid retrieval: BM25 (lexical) + multilingual embeddings (semantic).

One implementation shared by ``engine.query_memory``, the per-prompt recall
hook and the daemon, so ranking cannot drift between them.

Why both legs (audit #4, measured on a real 419-entry store):
* lexical only  - blind to Ukrainian prompts against English entries (0/8
  topics) but unbeatable on code identifiers;
* semantic only - cross-lingual (7/8) yet weaker on exact symbol names.

Why BM25 instead of the old overlap count: no length normalisation and no
term rarity meant long "umbrella" entries matched everything (one note was
surfaced 50 times, the top-10 entries took 35% of all recall slots).
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

STOP = frozenset((
    "the and for with that this from into are was were will would should could "
    "can not you your our all any has have had been being does did doing when "
    "where what which while about after before because between during under "
    "over then than them they there here how why yes okay please thanks thank "
    "sure hello continue its also but only now new use used using via per "
    "але або щоб для при цьому так добре давай зроби його вона вони має бути "
    "було щоби який яка яке більш дуже також треба можна після перед коли де "
    "что как это для или при так уже").split())

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}|[Ѐ-ӿ]{3,}")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def tokenize(text: str) -> List[str]:
    """Lower-cased word tokens; identifiers also yield their camel/snake parts
    (``attachGraceNotes`` -> attachgracenotes, attach, grace, notes)."""
    out: List[str] = []
    for w in _WORD_RE.findall(text or ""):
        lw = w.lower()
        if len(lw) >= 3 and lw not in STOP:
            out.append(lw)
        if "_" in w or (w != lw and w != w.upper()):
            for part in _CAMEL_RE.findall(w.replace("_", " ")):
                lp = part.lower()
                if len(lp) >= 3 and lp != lw and lp not in STOP:
                    out.append(lp)
    return out


def entry_text(e: Dict[str, Any]) -> str:
    """Text used for the semantic leg (bounded so umbrella entries don't
    dilute into a generic average)."""
    return " ".join(filter(None, [
        e.get("description") or "",
        (e.get("cause") or "")[:400],
        (e.get("fix") or "")[:700],
        " ".join(e.get("decisions") or [])[:300],
    ]))


def entry_tokens(e: Dict[str, Any]) -> List[str]:
    """Field-weighted token stream: symbols x3, description x2, body x1."""
    sym: List[str] = []
    for fn in e.get("functions") or []:
        sym += tokenize(str(fn))
    for f in e.get("files") or []:
        base = str(f).replace("\\", "/").rsplit("/", 1)[-1]
        sym += tokenize(base.rsplit(".", 1)[0])
    desc = tokenize(e.get("description") or "")
    body = tokenize(" ".join([e.get("cause") or "", e.get("fix") or "",
                              " ".join(e.get("decisions") or []),
                              " ".join(t for t in (e.get("tags") or []) if ":" not in t)]))
    return sym * 3 + desc * 2 + body


class BM25:
    def __init__(self, docs: Sequence[Sequence[str]], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.len = [len(d) for d in docs]
        self.avg = (sum(self.len) / self.n) if self.n else 0.0
        self.tf: List[Dict[str, int]] = []
        df: Dict[str, int] = {}
        for d in docs:
            c: Dict[str, int] = {}
            for t in d:
                c[t] = c.get(t, 0) + 1
            self.tf.append(c)
            for t in c:
                df[t] = df.get(t, 0) + 1
        self.idf = {t: math.log(1 + (self.n - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def score(self, q: Sequence[str]) -> Tuple[List[float], List[int]]:
        """Return (scores, distinct matched query terms) per document."""
        qs = [t for t in dict.fromkeys(q) if t in self.idf]
        scores = [0.0] * self.n
        matched = [0] * self.n
        for i in range(self.n):
            tf = self.tf[i]
            norm = self.k1 * (1 - self.b + self.b * (self.len[i] / self.avg if self.avg else 1.0))
            s = 0.0
            m = 0
            for t in qs:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + norm)
                    m += 1
            scores[i] = s
            matched[i] = m
        return scores, matched


# ── per-process index cache (the daemon reuses it between prompts) ──────────

_INDEX: Dict[int, Tuple[BM25, Optional[Any]]] = {}


def _signature(entries: Sequence[Dict[str, Any]], texts: Sequence[str]) -> int:
    return hash(tuple((e.get("id"), hash(t), len(e.get("functions") or []), len(e.get("files") or []))
                      for e, t in zip(entries, texts)))


def _index(entries: Sequence[Dict[str, Any]], semantic: bool) -> Tuple[BM25, Optional[Any]]:
    texts = [entry_text(e) for e in entries]
    sig = hash((_signature(entries, texts), semantic))
    hit = _INDEX.get(sig)
    if hit is not None:
        return hit
    bm = BM25([entry_tokens(e) for e in entries])
    mat = None
    if semantic and entries:
        try:
            from .embeddings import embed_passages
            vecs = embed_passages(texts)
            try:
                import numpy as np  # type: ignore
                mat = np.asarray(vecs, dtype="float32")
            except Exception:
                mat = [list(v) for v in vecs]
        except Exception:
            mat = None
    if len(_INDEX) > 24:
        _INDEX.clear()
    _INDEX[sig] = (bm, mat)
    return bm, mat


def _semantic_scores(mat: Any, query: str, n: int) -> Optional[List[float]]:
    if mat is None or n == 0:
        return None
    try:
        from .embeddings import embed_query
        qv = embed_query(query)
        try:
            import numpy as np  # type: ignore
            if hasattr(mat, "shape"):
                if mat.shape[1] != len(qv):
                    return None
                return (mat @ np.asarray(qv, dtype="float32")).tolist()
        except ImportError:
            pass
        if len(mat[0]) != len(qv):
            return None
        return [sum(a * b for a, b in zip(row, qv)) for row in mat]
    except Exception:
        return None


def rank(entries: Sequence[Dict[str, Any]], query: str, semantic: bool = True) -> List[Dict[str, float]]:
    """Score every entry against the query.

    Returns one dict per entry (aligned with ``entries``):
      relevance  0..1 blend used for ordering (0.65 semantic + 0.35 lexical)
      bm25       raw BM25 score
      matched    distinct query terms found in the entry
      sem        raw cosine (None-equivalent 0.0 when the leg is unavailable)
      z          how many std-devs the cosine stands above the store average -
                 a model-agnostic "is this actually about the query" signal
    """
    n = len(entries)
    if n == 0:
        return []
    bm, mat = _index(entries, semantic)
    bscores, matched = bm.score(tokenize(query))
    sscores = _semantic_scores(mat, query, n) if semantic else None

    bmax = max(bscores) if bscores else 0.0
    bn = [(s / bmax if bmax > 0 else 0.0) for s in bscores]
    have_sem = sscores is not None
    if have_sem:
        lo, hi = min(sscores), max(sscores)
        sn = [((s - lo) / (hi - lo) if hi > lo else 1.0) for s in sscores]
        mean = sum(sscores) / n
        std = math.sqrt(sum((s - mean) ** 2 for s in sscores) / n) or 1e-9
        z = [(s - mean) / std for s in sscores]
    else:
        sn, z, sscores = [0.0] * n, [0.0] * n, [0.0] * n

    out = []
    for i in range(n):
        if have_sem:
            rel = (0.65 * sn[i] + 0.35 * bn[i]) if bmax > 0 else sn[i]
        else:
            rel = bn[i]
        out.append({"relevance": rel, "bm25": bscores[i], "matched": float(matched[i]),
                    "sem": sscores[i], "z": z[i]})
    return out
