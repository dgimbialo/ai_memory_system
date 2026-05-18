"""Git-based stale-entry detector.

Inspects the working tree of a git repository to determine whether the
files and functions referenced by memory entries still exist.

All git queries run via subprocess — no external libraries required.

Typical usage (via MemoryEngine.check_stale):

    python run.py --project my_project check_stale \\
        --repo-path D:/WORK_PROJECTS/my_project --dry-run
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


class GitInspector:
    """Query a git repository for file/function existence and change history."""

    def __init__(self, repo_path: str | Path) -> None:
        self.repo_path = Path(repo_path).resolve()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def function_exists(self, function_name: str, file_path: str) -> bool:
        """Return True if *function_name* appears anywhere in *file_path*.

        Uses ``git grep`` so it respects the working-tree state (not just
        the index).  Falls back to a plain file scan if git is unavailable.

        Parameters
        ----------
        function_name : str   Identifier to search for (exact string match).
        file_path     : str   Repo-relative path to the source file.
        """
        if not function_name or not file_path:
            return True  # can't check — assume it exists to avoid false positives

        abs_file = self.repo_path / file_path
        if not abs_file.exists():
            return False

        # Try git grep first (fastest)
        result = self._run_git(
            ["grep", "-l", "--fixed-strings", function_name, "--", file_path]
        )
        if result is not None:
            return bool(result.strip())

        # Fallback: plain text search
        try:
            return function_name in abs_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return True  # can't read — assume exists

    def file_exists(self, file_path: str) -> bool:
        """Return True if *file_path* exists in the working tree.

        Parameters
        ----------
        file_path : str   Repo-relative path (e.g. 'src/main.cpp').
        """
        return (self.repo_path / file_path).exists()

    def changed_since(self, file_path: str, since_date: str) -> bool:
        """Return True if *file_path* has git commits more recent than *since_date*.

        Parameters
        ----------
        file_path  : str   Repo-relative path.
        since_date : str   ISO-8601 date string (e.g. '2026-01-01T00:00:00+00:00').
        """
        if not file_path or not since_date:
            return False
        result = self._run_git(
            ["log", f"--since={since_date}", "--oneline", "--", file_path]
        )
        if result is None:
            return False
        return bool(result.strip())

    def is_git_repo(self) -> bool:
        """Return True if *repo_path* is inside a git repository."""
        result = self._run_git(["rev-parse", "--git-dir"])
        return result is not None

    # ------------------------------------------------------------------
    # Stale entry analysis
    # ------------------------------------------------------------------

    def check_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Analyse a single memory entry for staleness.

        Checks:
        - Each file in ``entry["files"]`` still exists in the working tree.
        - Each function in ``entry["functions"]`` still appears in at least
          one of the entry's files.

        Returns
        -------
        dict with keys:
            entry_id     : str
            is_stale     : bool
            missing_files: list[str]   — files that no longer exist
            missing_fns  : list[str]   — functions not found in any listed file
            reason       : str         — human-readable summary
        """
        entry_id = entry.get("id", "")
        files = list(entry.get("files") or [])
        functions = list(entry.get("functions") or [])

        missing_files: List[str] = [f for f in files if not self.file_exists(f)]

        missing_fns: List[str] = []
        for fn in functions:
            # A function is considered present if it appears in ANY of the entry's files
            present = any(
                self.function_exists(fn, f)
                for f in files
                if self.file_exists(f)
            )
            if not present:
                missing_fns.append(fn)

        is_stale = bool(missing_files or missing_fns)
        parts: List[str] = []
        if missing_files:
            parts.append(f"missing file(s): {', '.join(missing_files)}")
        if missing_fns:
            parts.append(f"missing function(s): {', '.join(missing_fns)}")
        reason = "; ".join(parts) if parts else ""

        return {
            "entry_id":      entry_id,
            "is_stale":      is_stale,
            "missing_files": missing_files,
            "missing_fns":   missing_fns,
            "reason":        reason,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_git(self, args: List[str]) -> Optional[str]:
        """Run a git subcommand in *repo_path*.

        Returns stdout on success, ``None`` on any failure (git not found,
        non-zero exit, timeout).
        """
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                return result.stdout
            return None
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None
