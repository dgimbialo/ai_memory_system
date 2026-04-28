"""Atomic JSON storage layer with backup-before-write semantics."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()


class Storage:
    def __init__(self, data_dir: str | os.PathLike):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir = self.data_dir / ".backups"
        self.backup_dir.mkdir(exist_ok=True)

    def path(self, name: str) -> Path:
        return self.data_dir / name

    def read(self, name: str, default: Any) -> Any:
        p = self.path(name)
        if not p.exists():
            return default
        with _LOCK:
            try:
                with p.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                # Try recovering latest backup
                backup = self._latest_backup(name)
                if backup and backup.exists():
                    with backup.open("r", encoding="utf-8") as f:
                        return json.load(f)
                return default

    def write(self, name: str, data: Any) -> None:
        """Atomic write: backup existing -> tmp file -> os.replace."""
        with _LOCK:
            target = self.path(name)
            if target.exists():
                self._backup(name)
            # Atomic write via tmp file in same dir
            fd, tmp_path = tempfile.mkstemp(prefix=f".{name}.", dir=str(self.data_dir))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, target)
            except Exception:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise

    def append_log(self, name: str, entry: dict) -> None:
        """Append a single record to a JSON-list log file atomically."""
        with _LOCK:
            current = self.read(name, default=[])
            if not isinstance(current, list):
                current = []
            current.append(entry)
            self.write(name, current)

    def _backup(self, name: str) -> None:
        src = self.path(name)
        if not src.exists():
            return
        dst = self.backup_dir / f"{name}.bak"
        try:
            shutil.copy2(src, dst)
        except Exception:
            pass

    def _latest_backup(self, name: str) -> Path | None:
        candidate = self.backup_dir / f"{name}.bak"
        return candidate if candidate.exists() else None
