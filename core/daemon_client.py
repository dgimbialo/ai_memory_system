"""Thin stdlib client for the local memory daemon (server.py).

The daemon owns the warm embedding models and serves hybrid recall, so hooks
and CLI processes never pay a model load. Everything here is best-effort with
short timeouts: a missing daemon must never block or break a caller - the
caller falls back to local work and the daemon is (re)started in the
background for next time.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, List, Optional

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("AI_MEMORY_PORT", "5001"))
BASE = f"http://127.0.0.1:{PORT}"
# Bump when the daemon's HTTP contract changes: an older running daemon is
# asked to exit and a fresh one is spawned from the current code.
API_VERSION = 2

_STATE_FILE = ROOT / "data" / "daemon.json"
_SPAWN_STAMP = ROOT / "data" / "daemon.spawn"
_alive: Optional[bool] = None  # per-process memo


def _disabled() -> bool:
    return os.environ.get("AI_MEMORY_NO_DAEMON") == "1"


def _port_open(timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=timeout):
            return True
    except OSError:
        return False


def _request(path: str, payload: Any = None, timeout: float = 3.0) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def health(timeout: float = 0.6) -> bool:
    """True when a daemon speaking the current API version is reachable."""
    global _alive
    if _disabled():
        return False
    if _alive is not None:
        return _alive
    _alive = False
    if not _STATE_FILE.exists() or not _port_open():
        return False
    try:
        info = _request("/api/health", timeout=timeout)
    except Exception:
        return False
    if int(info.get("api_version") or 0) != API_VERSION:
        try:  # stale code: retire it, the next call spawns a fresh one
            _request("/api/shutdown", payload={}, timeout=1.0)
        except Exception:
            pass
        return False
    _alive = True
    return True


def ensure_daemon() -> bool:
    """Return health(); when down, spawn the daemon detached (not awaited)."""
    if _disabled():
        return False
    if health():
        return True
    try:
        if _SPAWN_STAMP.exists() and time.time() - _SPAWN_STAMP.stat().st_mtime < 45:
            return False  # someone just spawned it; don't stampede
        _SPAWN_STAMP.parent.mkdir(parents=True, exist_ok=True)
        _SPAWN_STAMP.write_text(str(time.time()), encoding="utf-8")
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")
        cmd = [str(pyw if pyw.exists() else exe), str(ROOT / "server.py"), "--no-browser", "--daemon"]
        kwargs: dict = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, cwd=str(ROOT), close_fds=True)
        if os.name == "nt":
            kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **kwargs)
    except Exception:
        pass
    return False


def embed(model: str, texts: List[str]) -> Optional[List[List[float]]]:
    if not health():
        ensure_daemon()
        return None
    try:
        return _request("/api/embed", {"model": model, "texts": texts},
                        timeout=20 + 0.2 * len(texts)).get("vectors")
    except Exception:
        return None


def recall(project: Optional[str], prompt: str, session_id: str = "",
           timeout: float = 4.0) -> Optional[str]:
    """Hybrid per-prompt recall rendered by the daemon; None when unavailable."""
    if not health():
        ensure_daemon()
        return None
    try:
        r = _request("/api/recall", {"project": project or "", "prompt": prompt,
                                     "session_id": session_id}, timeout=timeout)
        return r.get("text", "")
    except Exception:
        return None
