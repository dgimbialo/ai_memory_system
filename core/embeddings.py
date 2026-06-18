"""Embeddings module with graceful fallback.

Uses sentence-transformers (all-MiniLM-L6-v2) when available; otherwise falls
back to a deterministic hashing-based bag-of-words vector so the system stays
functional in offline / no-dependency environments.
"""
from __future__ import annotations

import functools
import math
import re
from typing import List, Optional

_MODEL = None
_BACKEND = None  # "st" or "fallback"


def _load_model():
    global _MODEL, _BACKEND
    if _BACKEND is not None:
        return _MODEL
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        _MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        _BACKEND = "st"
    except Exception:
        _MODEL = None
        _BACKEND = "fallback"
    return _MODEL


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _hash_embed(text: str, dim: int = 256) -> List[float]:
    vec = [0.0] * dim
    tokens = _TOKEN_RE.findall((text or "").lower())
    if not tokens:
        return vec
    for tok in tokens:
        h = hash(tok) % dim
        vec[h] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


@functools.lru_cache(maxsize=8192)
def _embed_cached(text: str) -> tuple:
    model = _load_model()
    if model is not None:
        try:
            v = model.encode(text, normalize_embeddings=True)
            return tuple(float(x) for x in v)
        except Exception:
            pass
    return tuple(_hash_embed(text))


def embed(text: str) -> List[float]:
    """Embed text, memoised by exact string.

    query_memory / deduplicate / conflict detection all re-embed the same entry
    texts repeatedly — once per query and once per pairwise pass. In the
    long-lived MCP server process this cache turns an O(N) re-encode on every
    query into a one-time cost per unique text. The tuple return keeps cached
    vectors immutable; callers receive a fresh list.
    """
    return list(_embed_cached(text or ""))


def cache_clear() -> None:
    """Drop the in-process embedding cache (e.g. after a model swap)."""
    _embed_cached.cache_clear()


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def similarity(text_a: str, text_b: str) -> float:
    return cosine(embed(text_a), embed(text_b))


def backend() -> str:
    _load_model()
    return _BACKEND or "fallback"
