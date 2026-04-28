"""VS Code + GitHub Copilot infrastructure builder.

Creates and maintains the file structure required for GitHub Copilot in any
project folder, and tracks which projects are managed by this system.

Idempotent: safe to call multiple times — existing files are merged, not
overwritten; missing files are created from templates.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
DATA_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "data"

# Default VS Code settings that enable and configure GitHub Copilot
VSCODE_SETTINGS_DEFAULT: Dict[str, Any] = {
    "github.copilot.enable": {"*": True},
    "github.copilot.chat.localeOverride": "auto",
    "github.copilot.renameSuggestions.triggerAutomatically": True,
    "chat.agent.enabled": True,
    "chat.editing.confirmEditRequestRetry": False,
    "editor.inlineSuggest.enabled": True,
}

EXTENSIONS_DEFAULT: Dict[str, Any] = {
    "recommendations": [
        "github.copilot",
        "github.copilot-chat",
    ]
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VSCodeInfraBuilder:
    """Creates and maintains GitHub Copilot infrastructure in project folders."""

    MANAGED_FILE = "managed_projects.json"

    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR_DEFAULT
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._managed_path = self.data_dir / self.MANAGED_FILE
        if not self._managed_path.exists():
            self._write_managed([])

    # ---------- managed project registry ----------

    def _read_managed(self) -> List[Dict[str, Any]]:
        try:
            with self._managed_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _write_managed(self, projects: List[Dict[str, Any]]) -> None:
        with self._managed_path.open("w", encoding="utf-8") as f:
            json.dump(projects, f, indent=2, ensure_ascii=False)

    def list_managed(self) -> List[Dict[str, Any]]:
        """Return all registered managed projects."""
        return self._read_managed()

    def _register(self, project_path: Path) -> None:
        projects = self._read_managed()
        path_str = str(project_path)
        for p in projects:
            if p.get("path") == path_str:
                p["last_updated"] = _now()
                self._write_managed(projects)
                return
        projects.append({
            "path": path_str,
            "name": project_path.name,
            "registered_at": _now(),
            "last_updated": _now(),
        })
        self._write_managed(projects)

    # ---------- infrastructure creation ----------

    def create_in(
        self,
        project_path: str | Path,
        project_name: Optional[str] = None,
        language: Optional[str] = None,
        framework: Optional[str] = None,
        extra_instructions: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create GitHub Copilot infrastructure in the given project folder.

        Returns a dict with ``created`` and ``skipped`` lists.
        """
        target = Path(project_path).resolve()
        if not target.exists():
            raise FileNotFoundError(f"Project path does not exist: {target}")

        name = project_name or target.name
        created: List[str] = []
        skipped: List[str] = []

        # ── .github/ ───────────────────────────────────────────────────────
        github_dir = target / ".github"
        github_dir.mkdir(exist_ok=True)

        # .github/copilot-instructions.md
        instructions_file = github_dir / "copilot-instructions.md"
        if not instructions_file.exists():
            content = self._render_instructions(name, language, framework, extra_instructions)
            instructions_file.write_text(content, encoding="utf-8")
            created.append(str(instructions_file.relative_to(target)))
        else:
            skipped.append(str(instructions_file.relative_to(target)))

        # .github/instructions/ (scoped instruction files)
        (github_dir / "instructions").mkdir(exist_ok=True)

        # .github/prompts/ (reusable prompt files)
        (github_dir / "prompts").mkdir(exist_ok=True)

        # ── .vscode/ ───────────────────────────────────────────────────────
        vscode_dir = target / ".vscode"
        vscode_dir.mkdir(exist_ok=True)

        # .vscode/settings.json
        settings_file = vscode_dir / "settings.json"
        if not settings_file.exists():
            settings_file.write_text(
                json.dumps(VSCODE_SETTINGS_DEFAULT, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            created.append(str(settings_file.relative_to(target)))
        else:
            self._merge_settings(settings_file)
            skipped.append(str(settings_file.relative_to(target)))

        # .vscode/extensions.json
        ext_file = vscode_dir / "extensions.json"
        if not ext_file.exists():
            ext_file.write_text(
                json.dumps(EXTENSIONS_DEFAULT, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            created.append(str(ext_file.relative_to(target)))
        else:
            self._merge_extensions(ext_file)
            skipped.append(str(ext_file.relative_to(target)))

        # Register the project
        self._register(target)

        return {
            "project": str(target),
            "created": created,
            "skipped_merged": skipped,
            "timestamp": _now(),
        }

    # ---------- instruction rendering ----------

    def _render_instructions(
        self,
        project_name: str,
        language: Optional[str],
        framework: Optional[str],
        extra: Optional[str],
    ) -> str:
        tpl_file = TEMPLATES_DIR / "copilot_instructions.md.tpl"
        if tpl_file.exists():
            raw = tpl_file.read_text(encoding="utf-8")
            tpl = Template(raw)
            return tpl.safe_substitute(
                project_name=project_name,
                language=language or "not specified",
                framework=framework or "not specified",
                extra_instructions=extra or "",
                generated_at=_now(),
            )
        return _default_instructions(project_name, language, framework, extra)

    # ---------- settings / extensions merging ----------

    def _merge_settings(self, settings_file: Path) -> None:
        """Add Copilot keys to an existing settings.json without overwriting."""
        try:
            with settings_file.open("r", encoding="utf-8") as f:
                settings: Dict[str, Any] = json.load(f)
        except (json.JSONDecodeError, OSError):
            settings = {}

        changed = False
        for key, value in VSCODE_SETTINGS_DEFAULT.items():
            if key not in settings:
                settings[key] = value
                changed = True

        if changed:
            with settings_file.open("w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)

    def _merge_extensions(self, ext_file: Path) -> None:
        """Add Copilot recommendations to extensions.json without duplicates."""
        try:
            with ext_file.open("r", encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}

        recs: List[str] = data.setdefault("recommendations", [])
        changed = False
        for ext in EXTENSIONS_DEFAULT["recommendations"]:
            if ext not in recs:
                recs.append(ext)
                changed = True

        if changed:
            with ext_file.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    # ---------- targeted instruction update ----------

    def update_instructions(
        self,
        project_path: str | Path,
        section: str,
        content: str,
    ) -> bool:
        """Update (or append) a named section inside copilot-instructions.md.

        Returns True if the file was updated, False if it does not exist.
        """
        target = Path(project_path).resolve()
        instructions_file = target / ".github" / "copilot-instructions.md"
        if not instructions_file.exists():
            return False

        text = instructions_file.read_text(encoding="utf-8")
        header = f"## {section}"
        pattern = re.compile(
            rf"(## {re.escape(section)}\n)(.*?)(?=\n## |\Z)", re.DOTALL
        )
        if pattern.search(text):
            updated = pattern.sub(
                lambda _m: f"## {section}\n{content.strip()}\n", text
            )
        else:
            updated = text.rstrip() + f"\n\n## {section}\n{content.strip()}\n"

        instructions_file.write_text(updated, encoding="utf-8")
        self._register(target)
        return True


# ---------- fallback template ----------

def _default_instructions(
    project_name: str,
    language: Optional[str],
    framework: Optional[str],
    extra: Optional[str],
) -> str:
    lines = [
        f"# {project_name} — Copilot Instructions",
        f"<!-- Auto-generated by AI Memory System on {_now()} -->",
        "",
        "## Project Overview",
        f"- **Project**: {project_name}",
        f"- **Language**: {language or 'not specified'}",
        f"- **Framework**: {framework or 'not specified'}",
        "",
        "## Coding Standards",
        "- Follow existing code style and naming conventions.",
        "- Prefer clear, readable code over clever one-liners.",
        "- Add meaningful variable names and keep functions small.",
        "- Write tests for new functionality.",
        "",
        "## Patterns and Conventions",
        "- Document public APIs with docstrings.",
        "- Validate inputs at system boundaries.",
        "- Avoid silent failures; raise or log errors explicitly.",
        "- Keep commits small and focused.",
        "",
    ]
    if extra:
        lines += ["## Additional Instructions", extra.strip(), ""]
    lines += [
        "## Learned Context",
        "<!-- This section is auto-updated by the AI Memory System learner. -->",
        "",
    ]
    return "\n".join(lines)
