"""CLI for the AI Memory System.

All operations route through MemoryEngine — no direct file access.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.engine import MemoryEngine  # noqa: E402

DATA_DIR = ROOT / "data"


def _project_slug(project: str) -> str:
    """Convert a project name or path to a safe directory slug."""
    from pathlib import PurePath
    slug = PurePath(project).name  # last component of path or plain name
    slug = slug.strip().lower().replace(" ", "_")
    return slug or "default"


def _data_dir(project: str | None) -> Path:
    if project:
        return DATA_DIR / "projects" / _project_slug(project)
    return DATA_DIR


def _engine(project: str | None = None) -> MemoryEngine:
    return MemoryEngine(_data_dir(project))


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def cmd_add_memory(args: argparse.Namespace) -> int:
    tags = list(args.tags or [])
    if args.project:
        slug = _project_slug(args.project)
        project_tag = f"project:{slug}"
        if project_tag not in tags:
            tags.append(project_tag)
    payload = {
        "type": args.type,
        "description": args.description,
        "cause": args.cause or "",
        "fix": args.fix or "",
        "files": args.files or [],
        "status": args.status,
        "confidence": args.confidence,
        "tags": tags,
    }
    result = _engine(args.project).add_memory(payload)
    _print(result)
    return 0


def cmd_list_memory(args: argparse.Namespace) -> int:
    rows = _engine(args.project).list_memory(type_=args.type, status=args.status)
    _print(rows)
    return 0


def cmd_detect_conflicts(args: argparse.Namespace) -> int:
    conflicts = _engine(args.project).detect_conflicts()
    _print(conflicts)
    return 0


def cmd_query_memory(args: argparse.Namespace) -> int:
    results = _engine(args.project).query_memory(args.query, top_k=args.top_k)
    _print(results)
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    _print(_engine(args.project).state())
    return 0


def cmd_update_status(args: argparse.Namespace) -> int:
    res = _engine(args.project).update_status(args.id, args.status, reason=args.reason or "")
    _print(res)
    return 0


def cmd_update_confidence(args: argparse.Namespace) -> int:
    res = _engine(args.project).update_confidence(args.id, args.confidence, reason=args.reason or "")
    _print(res)
    return 0


def cmd_render_wiki(args: argparse.Namespace) -> int:
    report = _engine(args.project).render_wiki_md()
    _print(report)
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    from core.lint import Linter

    linter = Linter(_data_dir(args.project))
    report = linter.run(stale_days=args.stale_days, low_confidence=args.low_confidence)
    _print(report)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ai_memory_system", description="Persistent AI memory CLI")
    p.add_argument(
        "--project", "-p",
        default=None,
        metavar="NAME_OR_PATH",
        help="Project name or path (e.g. my_project or C:/my_project). "
             "Data is isolated under data/projects/<name>/. "
             "Omit to use the default shared store.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add_memory", help="Add a new memory entry")
    a.add_argument("--type", required=True, choices=["bug_fix", "feature", "note", "decision"])
    a.add_argument("--description", required=True)
    a.add_argument("--cause", default="")
    a.add_argument("--fix", default="")
    a.add_argument("--files", nargs="*", default=[])
    a.add_argument("--status", default="active", choices=["active", "resolved", "conflict"])
    a.add_argument("--confidence", type=float, default=0.5)
    a.add_argument("--tags", nargs="*", default=[])
    a.set_defaults(func=cmd_add_memory)

    l = sub.add_parser("list_memory", help="List memory entries")
    l.add_argument("--type", default=None)
    l.add_argument("--status", default=None)
    l.set_defaults(func=cmd_list_memory)

    d = sub.add_parser("detect_conflicts", help="Run full conflict scan")
    d.set_defaults(func=cmd_detect_conflicts)

    q = sub.add_parser("query_memory", help="Semantic query over memory")
    q.add_argument("query")
    q.add_argument("--top-k", type=int, default=5)
    q.set_defaults(func=cmd_query_memory)

    s = sub.add_parser("state", help="Show engine state summary")
    s.set_defaults(func=cmd_state)

    us = sub.add_parser("update_status", help="Update an entry's status")
    us.add_argument("--id", required=True)
    us.add_argument("--status", required=True, choices=["active", "resolved", "conflict"])
    us.add_argument("--reason", default="")
    us.set_defaults(func=cmd_update_status)

    uc = sub.add_parser("update_confidence", help="Update an entry's confidence")
    uc.add_argument("--id", required=True)
    uc.add_argument("--confidence", type=float, required=True)
    uc.add_argument("--reason", default="")
    uc.set_defaults(func=cmd_update_confidence)

    rw = sub.add_parser("render_wiki", help="Render markdown wiki under data/wiki/")
    rw.set_defaults(func=cmd_render_wiki)

    ln = sub.add_parser("lint", help="Health-check the memory store")
    ln.add_argument("--stale-days", type=int, default=180,
                    help="Active entries older than this are flagged stale (default: 180)")
    ln.add_argument("--low-confidence", type=float, default=0.3,
                    help="Confidence below this is flagged (default: 0.3)")
    ln.set_defaults(func=cmd_lint)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
