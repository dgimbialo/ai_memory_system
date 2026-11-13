"""Embeddings with two roles, a disk cache and an optional daemon.

Roles
-----
* **sim** (``embed`` / ``similarity``): all-MiniLM-L6-v2. Every dedup, conflict
  and auto-merge threshold in the system was calibrated on this model's cosine
  distribution, so it stays in charge of "are these two entries the same".
* **retrieval** (``embed_query`` / ``embed_passages``): multilingual-e5-small.
  Measured on a real 419-entry English store with Ukrainian prompts (audit #4):
  relevant entry in top-3 for 7/8 topics versus 2/8 with MiniLM. e5 compresses
  all cosines into ~0.80-0.92, which would wreck the sim thresholds (7514 pairs
  above the 0.88 dedup line instead of 2) - hence two models, not one.

Cache
-----
Vectors are memoised in-process and persisted in ``data/embeddings.db`` keyed by
sha1(model + text). A fresh CLI or hook process therefore never re-encodes the
store (audit #4: 23 s cold query, 40 model passes per add).

Daemon
------
Entry points that opt in with ``use_daemon(True)`` ask the local memory daemon
to encode cache misses, so they never load a model themselves. Library use and
tests stay self-contained. Without sentence-transformers everything falls back
to a deterministic hashing vector.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import threading
import time
from array import array
from pathlib import Path
from typing import Dict, List, Optional, Sequence

SIM_MODEL = os.environ.get("AI_MEMORY_SIM_MODEL", "all-MiniLM-L6-v2")
RET_MODEL = os.environ.get("AI_MEMORY_RET_MODEL", "intfloat/multilingual-e5-small")

_ROOT = Path(__file__).resolve().parent.parent
_MODELS: Dict[str, object] = {}
_BACKEND: Optional[str] = None  # "st" | "fallback"
_LOCK = threading.RLock()
_MEM: Dict[str, tuple] = {}     # in-process memo: cache key -> vector
_MEM_MAX = 20000
_USE_DAEMON = False
_LAST_USED = 0.0


def use_daemon(enabled: bool = True) -> None:
    """Opt in to encoding cache misses through the local memory daemon."""
    global _USE_DAEMON
    _USE_DAEMON = bool(enabled) and os.environ.get("AI_MEMORY_NO_DAEMON") != "1"


# ── models ───────────────────────────────────────────────────────────────────

def _load_model(name: str):
    global _BACKEND
    with _LOCK:
        if name in _MODELS:
            return _MODELS[name]
        if _BACKEND == "fallback":
            return None
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            _MODELS[name] = SentenceTransformer(name)
            _BACKEND = "st"
        except Exception:
            if not _MODELS:
                _BACKEND = "fallback"
            return None
        return _MODELS[name]


def unload_models() -> None:
    """Free model memory (the daemon calls this after an idle period)."""
    with _LOCK:
        _MODELS.clear()


def idle_seconds() -> float:
    return time.time() - _LAST_USED if _LAST_USED else float("inf")


def models_loaded() -> List[str]:
    return list(_MODELS)


# ── deterministic fallback ───────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[Ѐ-ӿ]+")


def _hash_embed(text: str, dim: int = 256) -> List[float]:
    vec = [0.0] * dim
    tokens = _TOKEN_RE.findall((text or "").lower())
    if not tokens:
        return vec
    for tok in tokens:
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16) % dim
        vec[h] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


# ── disk cache ───────────────────────────────────────────────────────────────

def _cache_path() -> Optional[Path]:
    v = os.environ.get("AI_MEMORY_EMBED_CACHE", "")
    if v.lower() == "off":
        return None
    return Path(v) if v else _ROOT / "data" / "embeddings.db"


_DISK_READY: set = set()


def _disk() -> Optional[sqlite3.Connection]:
    p = _cache_path()
    if p is None:
        return None
    try:
        con = sqlite3.connect(str(p), timeout=10, isolation_level=None)
        if str(p) not in _DISK_READY:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE IF NOT EXISTS emb (k TEXT PRIMARY KEY, v BLOB NOT NULL, ts INTEGER)")
            _DISK_READY.add(str(p))
        con.execute("PRAGMA synchronous=OFF")
        return con
    except (sqlite3.Error, OSError):
        return None


def _key(model: str, text: str) -> str:
    return hashlib.sha1((model + "\x00" + text).encode("utf-8")).hexdigest()


def _disk_get(keys: Sequence[str]) -> Dict[str, tuple]:
    con = _disk()
    if con is None or not keys:
        return {}
    out: Dict[str, tuple] = {}
    try:
        for i in range(0, len(keys), 500):
            chunk = list(keys[i:i + 500])
            q = "SELECT k, v FROM emb WHERE k IN (%s)" % ",".join("?" * len(chunk))
            for k, blob in con.execute(q, chunk):
                a = array("f")
                a.frombytes(blob)
                out[k] = tuple(a)
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return out


def _disk_put(items: Dict[str, tuple]) -> None:
    con = _disk()
    if con is None or not items:
        return
    try:
        now = int(time.time())
        con.execute("BEGIN")
        con.executemany("INSERT OR REPLACE INTO emb(k, v, ts) VALUES (?, ?, ?)",
                        [(k, array("f", v).tobytes(), now) for k, v in items.items()])
        con.execute("COMMIT")
    except sqlite3.Error:
        pass
    finally:
        con.close()


# ── encoding ─────────────────────────────────────────────────────────────────

def _encode_local(model_name: str, texts: List[str]) -> Optional[List[tuple]]:
    model = _load_model(model_name)
    if model is None:
        return None
    try:
        vecs = model.encode(texts, normalize_embeddings=True, batch_size=32)  # type: ignore[attr-defined]
        return [tuple(float(x) for x in v) for v in vecs]
    except Exception:
        return None


def _encode_remote(model_name: str, texts: List[str]) -> Optional[List[tuple]]:
    try:
        from . import daemon_client
        vecs = daemon_client.embed(model_name, texts)
    except Exception:
        return None
    if not vecs or len(vecs) != len(texts):
        return None
    return [tuple(v) for v in vecs]


def _encode(model_name: str, texts: List[str], allow_remote: bool) -> tuple:
    """Return (vectors, real): real=False when the hashing fallback was used."""
    global _LAST_USED
    _LAST_USED = time.time()
    vecs = None
    if allow_remote and _USE_DAEMON:
        vecs = _encode_remote(model_name, texts)
    if vecs is None:
        vecs = _encode_local(model_name, texts)
    if vecs is None:
        return [tuple(_hash_embed(t)) for t in texts], False
    return vecs, True


def encode_raw(model_name: str, texts: List[str]) -> List[tuple]:
    """Encode locally with no caching (the daemon's own entry point)."""
    return _encode(model_name, texts, allow_remote=False)[0]


def _embed_many(model_name: str, texts: List[str]) -> List[tuple]:
    keys = [_key(model_name, t) for t in texts]
    out: Dict[str, tuple] = {k: _MEM[k] for k in keys if k in _MEM}
    missing = [k for k in dict.fromkeys(keys) if k not in out]
    if missing:
        found = _disk_get(missing)
        out.update(found)
        still = [k for k in missing if k not in found]
        if still:
            by_key = dict(zip(keys, texts))
            vecs, real = _encode(model_name, [by_key[k] for k in still], allow_remote=True)
            fresh = dict(zip(still, vecs))
            out.update(fresh)
            if real:  # never persist hashing-fallback vectors
                _disk_put(fresh)
        if len(_MEM) > _MEM_MAX:
            _MEM.clear()
        _MEM.update({k: out[k] for k in missing})
    return [out[k] for k in keys]


def _prefix(model_name: str, kind: str) -> str:
    # The e5 family is trained with asymmetric "query: " / "passage: " prefixes.
    return f"{kind}: " if "e5" in model_name.lower() else ""


# ── public API ───────────────────────────────────────────────────────────────

def embed(text: str) -> List[float]:
    """Similarity-role vector (dedup, conflicts, auto-merge thresholds)."""
    return list(_embed_many(SIM_MODEL, [text or ""])[0])


def embed_query(text: str) -> List[float]:
    """Retrieval-role vector for a search query / user prompt."""
    return list(_embed_many(RET_MODEL, [_prefix(RET_MODEL, "query") + (text or "")])[0])


def embed_passages(texts: Sequence[str]) -> List[tuple]:
    """Retrieval-role vectors for stored entries (batched, cached on disk)."""
    p = _prefix(RET_MODEL, "passage")
    return _embed_many(RET_MODEL, [p + (t or "") for t in texts])


def cache_clear() -> None:
    """Drop the in-process embedding memo (the disk cache is untouched)."""
    _MEM.clear()


_sumprod = getattr(math, "sumprod", None)  # C-speed dot product (Python 3.12+)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    if _sumprod is not None:
        dot, na2, nb2 = _sumprod(a, b), _sumprod(a, a), _sumprod(b, b)
    else:
        dot = sum(x * y for x, y in zip(a, b))
        na2 = sum(x * x for x in a)
        nb2 = sum(y * y for y in b)
    return dot / ((math.sqrt(na2) or 1.0) * (math.sqrt(nb2) or 1.0))


def embed_batch(texts: Sequence[str]) -> List[tuple]:
    """Similarity-role vectors for many texts at once (cache warming)."""
    return _embed_many(SIM_MODEL, [t or "" for t in texts])


def similarity(text_a: str, text_b: str) -> float:
    va, vb = _embed_many(SIM_MODEL, [text_a or "", text_b or ""])
    return cosine(va, vb)


def backend() -> str:
    if _BACKEND is None:
        if _USE_DAEMON:
            try:
                from . import daemon_client
                if daemon_client.health():
                    return "st"
            except Exception:
                pass
        _load_model(SIM_MODEL)
    return _BACKEND or "fallback"
