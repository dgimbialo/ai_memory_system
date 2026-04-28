"""Copilot Learner.

Analyses the Copilot activity log, extracts recurring patterns and topics,
and propagates the findings as an updated "Learned Context" section in every
managed project's ``.github/copilot-instructions.md``.

The learner also updates the master template file so that new projects
bootstrapped later inherit the accumulated knowledge.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "data"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Stop-word list (noise in VS Code debug logs) ──────────────────────────────

_STOPWORDS = frozenset([
    "that", "with", "from", "this", "have", "been", "will", "your",
    "their", "what", "when", "where", "which", "there", "about",
    "copilot", "github", "chat", "message", "request", "response",
    "extension", "vscode", "false", "true", "null", "undefined",
    "error", "warning", "info", "debug", "trace",
])

_LANG_KEYWORDS = frozenset([
    "python", "javascript", "typescript", "csharp", "java", "rust",
    "golang", "ruby", "swift", "kotlin", "html", "css", "scss",
    "bash", "powershell", "sql", "markdown",
])

_FRAMEWORK_KEYWORDS = frozenset([
    "react", "angular", "vue", "django", "flask", "fastapi",
    "express", "nextjs", "dotnet", "spring", "rails", "laravel",
    "pytest", "jest", "vitest", "playwright",
])

_TASK_MAP: Dict[str, str] = {
    "test": "testing",
    "debug": "debugging",
    "refactor": "refactoring",
    "document": "documentation",
    "optimize": "performance optimisation",
    "security": "security",
    "database": "database operations",
    "async": "async / concurrent programming",
    "deploy": "deployment",
    "docker": "containerisation",
    "migration": "data migrations",
}


class CopilotLearner:
    """Analyses activity and updates project instructions."""

    ACTIVITY_FILE = "copilot_activity.json"
    MANAGED_FILE = "managed_projects.json"
    LEARN_LOG_FILE = "learn_log.json"

    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR_DEFAULT

    # ── internal helpers ──────────────────────────────────────────────────

    def _read_json(self, filename: str, default: Any) -> Any:
        path = self.data_dir / filename
        if not path.exists():
            return default
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default

    def _write_json(self, filename: str, data: Any) -> None:
        path = self.data_dir / filename
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ── analysis ─────────────────────────────────────────────────────────

    def analyze(self) -> Dict[str, Any]:
        """Return a structured analysis of the copilot activity log."""
        activity: List[Dict[str, Any]] = self._read_json(self.ACTIVITY_FILE, [])
        if not activity:
            return {
                "total_events": 0,
                "keyword_freq": {},
                "patterns": [],
                "analyzed_at": _now(),
            }

        # Collect all free text from events
        texts: List[str] = []
        for ev in activity:
            for field in ("message", "raw", "content", "text"):
                val = ev.get(field)
                if isinstance(val, str):
                    texts.append(val.lower())

        # Word frequency (alpha words ≥ 4 chars, excluding stop-words)
        word_counter: Counter = Counter()
        for text in texts:
            words = re.findall(r"\b[a-z]{4,}\b", text)
            word_counter.update(w for w in words if w not in _STOPWORDS)

        top_keywords: Dict[str, int] = {
            w: c for w, c in word_counter.most_common(50) if c > 1
        }

        patterns = self._extract_patterns(word_counter)

        return {
            "total_events": len(activity),
            "keyword_freq": dict(list(top_keywords.items())[:20]),
            "patterns": patterns,
            "analyzed_at": _now(),
        }

    def _extract_patterns(self, word_counter: Counter) -> List[str]:
        patterns: List[str] = []
        words = set(word_counter.keys())

        found_langs = sorted(words & _LANG_KEYWORDS)
        if found_langs:
            patterns.append(f"Languages mentioned: {', '.join(found_langs)}")

        found_fw = sorted(words & _FRAMEWORK_KEYWORDS)
        if found_fw:
            patterns.append(f"Frameworks / libraries mentioned: {', '.join(found_fw)}")

        found_tasks = [
            label for kw, label in _TASK_MAP.items() if kw in words
        ]
        if found_tasks:
            patterns.append(f"Common task areas: {', '.join(found_tasks)}")

        return patterns

    # ── content builder ───────────────────────────────────────────────────

    def _build_learned_section(self, analysis: Dict[str, Any]) -> str:
        lines = [
            f"*Auto-updated by AI Memory System — {analysis.get('analyzed_at', _now())}*",
            f"*Based on {analysis['total_events']} Copilot activity event(s).*",
            "",
        ]
        if analysis.get("patterns"):
            lines.append("**Observed patterns:**")
            for p in analysis["patterns"]:
                lines.append(f"- {p}")
            lines.append("")
        if analysis.get("keyword_freq"):
            top = list(analysis["keyword_freq"].keys())[:10]
            lines.append(f"**Top topics**: {', '.join(top)}")
            lines.append("")
        return "\n".join(lines)

    # ── propagation ───────────────────────────────────────────────────────

    def update_all_managed(self, dry_run: bool = False) -> Dict[str, Any]:
        """Analyze activity and push learned context to all managed projects.

        Args:
            dry_run: if True, compute analysis but do NOT write any files.

        Returns a summary dict with analysis results and per-project status.
        """
        from .vscode_infra import VSCodeInfraBuilder

        analysis = self.analyze()
        managed: List[Dict[str, Any]] = self._read_json(self.MANAGED_FILE, [])
        updated: List[str] = []
        errors: List[str] = []

        if not analysis["patterns"] and not analysis.get("keyword_freq"):
            return {
                "skipped": "no patterns learned yet — run `scan` first",
                "managed_projects": len(managed),
                "dry_run": dry_run,
            }

        content = self._build_learned_section(analysis)
        builder = VSCodeInfraBuilder(self.data_dir)

        for project in managed:
            path = project.get("path", "")
            if not path or not Path(path).exists():
                errors.append(f"path not found: {path}")
                continue
            try:
                if not dry_run:
                    builder.update_instructions(path, "Learned Context", content)
                updated.append(path)
            except Exception as exc:
                errors.append(f"{path}: {exc}")

        result: Dict[str, Any] = {
            "analysis": analysis,
            "updated_projects": updated,
            "errors": errors,
            "dry_run": dry_run,
            "timestamp": _now(),
        }

        if not dry_run:
            learn_log: List[Any] = self._read_json(self.LEARN_LOG_FILE, [])
            learn_log.append(result)
            self._write_json(self.LEARN_LOG_FILE, learn_log)

        return result

    # ── self-update ───────────────────────────────────────────────────────

    def self_update_templates(self) -> Dict[str, Any]:
        """Rewrite the master template's Learned Patterns section.

        This ensures that projects created in the future start with up-to-date
        context baked into their copilot-instructions.md.
        """
        analysis = self.analyze()
        if not analysis.get("patterns"):
            return {"skipped": "no patterns to embed in templates"}

        tpl_file = TEMPLATES_DIR / "copilot_instructions.md.tpl"
        if not tpl_file.exists():
            return {"skipped": f"template not found: {tpl_file}"}

        text = tpl_file.read_text(encoding="utf-8")
        learned_content = self._build_learned_section(analysis)

        updated = re.sub(
            r"## Learned Patterns\n.*",
            f"## Learned Patterns\n{learned_content}",
            text,
            flags=re.DOTALL,
        )

        if updated == text:
            return {"skipped": "no 'Learned Patterns' section found in template"}

        tpl_file.write_text(updated, encoding="utf-8")
        return {
            "updated_template": str(tpl_file),
            "patterns_applied": analysis["patterns"],
            "timestamp": _now(),
        }
