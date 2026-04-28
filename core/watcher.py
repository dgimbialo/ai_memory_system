"""Background daemon: watches projects + Copilot logs simultaneously.

Architecture
------------
                ┌─────────────────────────────┐
  Copilot logs  │  CopilotActivityLogger.scan │  every `log_interval` seconds
                └──────────────┬──────────────┘
                               │ new chat events → copilot_activity.json
                ┌──────────────▼──────────────┐
  Project dirs  │  watchdog FileSystemHandler │  on any file save
                └──────────────┬──────────────┘
                               │ changed file + recent chat event
                ┌──────────────▼──────────────┐
                │      AutoMemory.on_file_changed │
                └──────────────┬──────────────┘
                               │ new memory entry
                ┌──────────────▼──────────────┐
                │ MemoryEngine + WikiRenderer  │
                └─────────────────────────────┘

Usage
-----
    python run_infra.py daemon                    # watches all managed projects
    python run_infra.py daemon --projects C:/App  # override project list
    python run_infra.py daemon --log-interval 10  # Copilot scan every 10 s
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

DATA_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "data"

# ── optional watchdog import ──────────────────────────────────────────────────

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent
    _HAS_WATCHDOG = True
except ImportError:
    _HAS_WATCHDOG = False


# ── fallback polling watcher (no watchdog) ────────────────────────────────────

class _PollingWatcher:
    """Dead-simple mtime-based polling watcher used when watchdog is absent."""

    def __init__(self, paths: List[Path], callback, interval: float = 2.0):
        self.paths = paths
        self.callback = callback
        self.interval = interval
        self._mtimes: dict[str, float] = {}
        self._stop = threading.Event()

    def start(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        return t

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            for root in self.paths:
                self._scan(root)
            time.sleep(self.interval)

    def _scan(self, root: Path):
        if not root.exists():
            return
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            key = str(p)
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            prev = self._mtimes.get(key)
            if prev is None:
                self._mtimes[key] = mtime
            elif mtime != prev:
                self._mtimes[key] = mtime
                self.callback(root, p)


# ── watchdog handler ──────────────────────────────────────────────────────────

if _HAS_WATCHDOG:
    class _WatchdogHandler(FileSystemEventHandler):
        def __init__(self, project_path: Path, callback):
            super().__init__()
            self.project_path = project_path
            self.callback = callback

        def on_modified(self, event):
            if not event.is_directory:
                self.callback(self.project_path, Path(event.src_path))

        def on_created(self, event):
            if not event.is_directory:
                self.callback(self.project_path, Path(event.src_path))


# ── daemon ────────────────────────────────────────────────────────────────────

class Daemon:
    """Watches project directories and Copilot logs; auto-creates memory entries."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        project_paths: Optional[List[Path]] = None,
        log_interval: float = 15.0,
        scan_interval: float = 2.0,
    ):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR_DEFAULT
        self.log_interval = log_interval
        self.scan_interval = scan_interval

        # Resolve project list
        if project_paths:
            self.project_paths = project_paths
        else:
            self.project_paths = self._load_managed_projects()

        # Lazy-import heavy modules
        from core.copilot_logger import CopilotActivityLogger
        from core.auto_memory import AutoMemory

        self.logger = CopilotActivityLogger(self.data_dir)
        self.auto_memory = AutoMemory(self.data_dir)

        self._stop_event = threading.Event()
        self._observers: list = []

    # ── project registry ──────────────────────────────────────────────────

    def _load_managed_projects(self) -> List[Path]:
        import json
        managed_file = self.data_dir / "managed_projects.json"
        if not managed_file.exists():
            return []
        try:
            with managed_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return [Path(p["path"]) for p in data if Path(p.get("path", "")).exists()]
        except Exception:
            return []

    # ── file change callback ──────────────────────────────────────────────

    def _on_file_changed(self, project_path: Path, file_path: Path):
        try:
            self.auto_memory.on_file_changed(project_path, file_path)
        except Exception as exc:
            print(f"[daemon] ⚠️  auto_memory error: {exc}")

    # ── start ─────────────────────────────────────────────────────────────

    def start(self, block: bool = True) -> None:
        if not self.project_paths:
            print("[daemon] ⚠️  No managed projects. Run `python run_infra.py setup <path>` first.")
            return

        print(f"[daemon] 🚀 Starting AI Memory Daemon")
        print(f"[daemon] 📁 Watching {len(self.project_paths)} project(s):")
        for p in self.project_paths:
            print(f"[daemon]    • {p}")
        print(f"[daemon] 🔍 Copilot log scan every {self.log_interval}s")
        print(f"[daemon] 💾 Using {'watchdog (fast)' if _HAS_WATCHDOG else 'polling (fallback)'} for file watching")
        print("[daemon] Press Ctrl-C to stop.\n")

        # Start file watchers
        if _HAS_WATCHDOG:
            for project_path in self.project_paths:
                handler = _WatchdogHandler(project_path, self._on_file_changed)
                observer = Observer()
                observer.schedule(handler, str(project_path), recursive=True)
                observer.start()
                self._observers.append(observer)
        else:
            poller = _PollingWatcher(
                self.project_paths,
                self._on_file_changed,
                interval=self.scan_interval,
            )
            poller.start()
            self._observers.append(poller)

        # Copilot log scanning in a background thread
        log_thread = threading.Thread(
            target=self._log_scan_loop, daemon=True
        )
        log_thread.start()

        if block:
            try:
                while not self._stop_event.is_set():
                    time.sleep(1)
            except KeyboardInterrupt:
                self.stop()

    def _log_scan_loop(self):
        while not self._stop_event.is_set():
            try:
                count = self.logger.scan_once()
                if count:
                    print(f"[daemon] 📨 +{count} new Copilot event(s)")
            except Exception as exc:
                print(f"[daemon] ⚠️  log scan error: {exc}")
            # Sleep in small increments so we can respond to stop quickly
            for _ in range(int(self.log_interval)):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    def stop(self):
        print("\n[daemon] Stopping…")
        self._stop_event.set()
        for obs in self._observers:
            try:
                obs.stop()
            except Exception:
                pass
        print("[daemon] Stopped.")
