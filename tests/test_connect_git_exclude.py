"""connect.py hides its machine-local config files from git.

The configs embed absolute paths of this machine, so in a git repo they go to
.git/info/exclude (local, never committed) unless git already ignores them.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import connect

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _write_config(project, name=".mcp.json", local=True):
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    command = str(connect.ROOT / ".venv" / "python.exe") if local else "python"
    path.write_text(json.dumps({"mcpServers": {"ai-memory": {"command": command}}}), encoding="utf-8")
    return path


def _exclude_lines(repo):
    f = repo / ".git" / "info" / "exclude"
    if not f.exists():
        return []
    return [l for l in f.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]


def _ignored(repo, name):
    return _git(repo, "check-ignore", "-q", "--", name).returncode == 0


class TestGitExclude:
    def test_local_configs_are_hidden(self, tmp_path):
        repo = _repo(tmp_path)
        _write_config(repo, ".mcp.json")
        _write_config(repo, ".claude/settings.json")
        report = connect._git_exclude_local_configs(repo)
        assert _exclude_lines(repo) == ["/.mcp.json", "/.claude/settings.json"]
        assert _ignored(repo, ".mcp.json") and _ignored(repo, ".claude/settings.json")
        assert len(report) == 1 and "hidden from git" in report[0]
        assert _git(repo, "status", "--porcelain").stdout.strip() == ""

    def test_idempotent(self, tmp_path):
        repo = _repo(tmp_path)
        _write_config(repo)
        connect._git_exclude_local_configs(repo)
        assert connect._git_exclude_local_configs(repo) == []
        assert _exclude_lines(repo) == ["/.mcp.json"]
        text = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        assert text.count(connect._EXCLUDE_HEADER) == 1

    def test_existing_exclude_content_is_kept(self, tmp_path):
        repo = _repo(tmp_path)
        exclude = repo / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("# mine\nbuild/", encoding="utf-8")  # no trailing newline
        _write_config(repo)
        connect._git_exclude_local_configs(repo)
        assert _exclude_lines(repo) == ["build/", "/.mcp.json"]

    def test_already_ignored_file_is_left_alone(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / ".gitignore").write_text(".mcp.json\n", encoding="utf-8")
        _write_config(repo)
        assert connect._git_exclude_local_configs(repo) == []
        assert _exclude_lines(repo) == []

    def test_tracked_file_is_reported_not_hidden(self, tmp_path):
        repo = _repo(tmp_path)
        _write_config(repo)
        _git(repo, "add", ".mcp.json")
        assert _git(repo, "commit", "-q", "-m", "add config").returncode == 0
        report = connect._git_exclude_local_configs(repo)
        assert _exclude_lines(repo) == []
        assert len(report) == 1 and "WARNING" in report[0] and ".mcp.json" in report[0]

    def test_config_without_local_paths_is_not_hidden(self, tmp_path):
        repo = _repo(tmp_path)
        _write_config(repo, ".vscode/settings.json", local=False)
        assert connect._git_exclude_local_configs(repo) == []
        assert _exclude_lines(repo) == []

    def test_project_in_subfolder_gets_anchored_path(self, tmp_path):
        repo = _repo(tmp_path)
        project = repo / "apps" / "tool"
        project.mkdir(parents=True)
        _write_config(project)
        _write_config(repo)  # same name at the repo root must stay visible
        connect._git_exclude_local_configs(project)
        assert _exclude_lines(repo) == ["/apps/tool/.mcp.json"]
        assert _ignored(repo, "apps/tool/.mcp.json")
        assert not _ignored(repo, ".mcp.json")

    def test_not_a_git_repo_is_a_noop(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        _write_config(plain)
        # tmp_path may itself sit inside a repo on a dev machine: only assert
        # that nothing is created in the plain folder and nothing raises.
        connect._git_exclude_local_configs(plain)
        assert not (plain / ".git").exists()

    def test_special_characters_are_escaped(self, tmp_path):
        assert connect._exclude_pattern("a[1]/x*.json") == "/a\\[1]/x\\*.json"
        assert connect._exclude_pattern("interactive_learn_C++/.mcp.json") == "/interactive_learn_C++/.mcp.json"
        # and git really matches the escaped pattern, literally
        repo = _repo(tmp_path)
        project = repo / "lab[1] C++"
        project.mkdir()
        _write_config(project)
        other = repo / "lab1 C++"
        other.mkdir()
        _write_config(other)
        connect._git_exclude_local_configs(project)
        assert _ignored(repo, "lab[1] C++/.mcp.json")
        assert not _ignored(repo, "lab1 C++/.mcp.json")
