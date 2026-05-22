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
        p = Path(project)
        # If caller passes an absolute existing path, use it directly as data dir
        if p.is_absolute() and p.exists():
            return p
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
        "functions": args.functions or [],
        "decisions": args.decisions or [],
        "status": args.status,
        "confidence": args.confidence,
        "tags": tags,
        "depends_on": args.depends_on or [],
        "test_ids": args.test_ids or [],
    }
    result = _engine(args.project).add_memory(payload)
    _print(result)
    return 0


def cmd_session_summary(args: argparse.Namespace) -> int:
    result = _engine(args.project).session_summary(
        description=args.description,
        tags=list(args.tags or []),
        since_n=args.since_n,
    )
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
    # Support both positional 'query' and --query flag
    q_text = getattr(args, 'query_text', None) or getattr(args, 'query', None) or ''
    results = _engine(args.project).query_memory(
        q_text,
        top_k=args.top_k,
        filter_file=getattr(args, 'filter_file', None),
        filter_function=getattr(args, 'filter_function', None),
        fmt=getattr(args, 'format', 'concise'),
    )
    _print(results)
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    _print(_engine(args.project).state())
    return 0


def cmd_update_status(args: argparse.Namespace) -> int:
    res = _engine(args.project).update_status(args.id, args.status, reason=args.reason or "")
    _print(res)
    return 0


def cmd_list_conflicts(args: argparse.Namespace) -> int:
    conflicts = _engine(args.project).list_conflicts()
    _print(conflicts)
    return 0


def cmd_resolve_conflict(args: argparse.Namespace) -> int:
    result = _engine(args.project).resolve_conflict(
        conflict_id=args.id,
        action=args.action,
        reason=args.reason or "",
    )
    _print(result)
    return 0


def cmd_update_confidence(args: argparse.Namespace) -> int:
    res = _engine(args.project).update_confidence(args.id, args.confidence, reason=args.reason or "")
    _print(res)
    return 0


def cmd_decay(args: argparse.Namespace) -> int:
    result = _engine(args.project).decay(
        dry_run=args.dry_run,
        half_life_days=args.half_life_days,
        min_confidence=args.min_confidence,
    )
    _print(result)
    return 0


def cmd_decay_preview(args: argparse.Namespace) -> int:
    rows = _engine(args.project).decay_preview()
    # Show only changed entries unless --all is passed
    if not args.all:
        rows = [r for r in rows if r.get("changed")]
    _print(rows)
    return 0


def cmd_deduplicate(args: argparse.Namespace) -> int:
    result = _engine(args.project).deduplicate(
        dry_run=args.dry_run,
        threshold=args.threshold,
    )
    _print(result)
    return 0


def cmd_find_duplicates(args: argparse.Namespace) -> int:
    clusters = _engine(args.project).find_duplicate_clusters(
        threshold=args.threshold,
    )
    if not clusters:
        _print({"clusters_found": 0, "message": "No duplicate clusters found."})
        return 0
    _print({"clusters_found": len(clusters), "clusters": clusters})
    return 0


def cmd_render_wiki(args: argparse.Namespace) -> int:
    report = _engine(args.project).render_wiki_md()
    _print(report)
    return 0


def cmd_add_link(args: argparse.Namespace) -> int:
    result = _engine(args.project).add_dependency_link(args.from_id, args.to_id)
    _print(result)
    return 0


def cmd_remove_link(args: argparse.Namespace) -> int:
    result = _engine(args.project).remove_dependency_link(args.from_id, args.to_id)
    _print(result)
    return 0


def cmd_get_dependencies(args: argparse.Namespace) -> int:
    result = _engine(args.project).get_dependencies(args.id, depth=args.depth)
    _print(result)
    return 0


def cmd_suggest_links(args: argparse.Namespace) -> int:
    result = _engine(args.project).suggest_dependency_links(
        args.id, threshold=args.threshold, top_k=args.top_k
    )
    _print(result)
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    from core.lint import Linter

    linter = Linter(_data_dir(args.project))
    report = linter.run(stale_days=args.stale_days, low_confidence=args.low_confidence)
    _print(report)
    return 0


def cmd_summarize_file(args: argparse.Namespace) -> int:
    result = _engine(args.project).summarize_file(args.file)
    _print(result)
    return 0


def cmd_check_stale(args: argparse.Namespace) -> int:
    result = _engine(args.project).check_stale(
        repo_path=args.repo_path,
        min_age_days=args.min_age_days,
        dry_run=args.dry_run,
    )
    _print(result)
    return 0


def cmd_stabilize_unstable(args: argparse.Namespace) -> int:
    result = _engine(args.project).stabilize_unstable_entries(
        min_stable_days=args.min_stable_days,
        dry_run=args.dry_run,
    )
    _print(result)
    return 0


def cmd_update_instructions(args: argparse.Namespace) -> int:
    result = _engine(args.project).update_instructions(
        project_path=args.project_path,
        min_confidence=args.min_confidence,
        dry_run=args.dry_run,
    )
    _print(result)
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
    a.add_argument("--functions", nargs="*", default=[],
                   help="Specific functions/methods changed (more granular than --files)")
    a.add_argument("--decisions", nargs="*", default=[],
                   help="Key architectural decisions with rationale (WHY, not just WHAT)")
    a.add_argument("--status", default="active", choices=["active", "resolved", "conflict", "superseded"])
    a.add_argument("--confidence", type=float, default=0.5)
    a.add_argument("--tags", nargs="*", default=[])
    a.add_argument("--depends-on", dest="depends_on", nargs="*", default=[],
                   help="IDs of entries this entry builds upon (creates dependency links)")
    a.add_argument("--test-ids", dest="test_ids", nargs="*", default=[],
                   help="Test names that verify this entry's behaviour (e.g. TestFoo::Bar)")
    a.set_defaults(func=cmd_add_memory)

    ss = sub.add_parser(
        "session_summary",
        help="Create a single summary entry for the current session "
             "(aggregates files/functions from recent entries)",
    )
    ss.add_argument("--description", required=True,
                    help="One-line summary of what this session accomplished")
    ss.add_argument("--tags", nargs="*", default=[])
    ss.add_argument("--since-n", dest="since_n", type=int, default=20,
                    help="How many recent entries to aggregate (default: 20)")
    ss.set_defaults(func=cmd_session_summary)

    l = sub.add_parser("list_memory", help="List memory entries")
    l.add_argument("--type", default=None)
    l.add_argument("--status", default=None)
    l.set_defaults(func=cmd_list_memory)

    d = sub.add_parser("detect_conflicts", help="Run full conflict scan")
    d.set_defaults(func=cmd_detect_conflicts)

    q = sub.add_parser("query_memory", help="Semantic query over memory")
    q.add_argument("query", nargs='?', default=None,
                   help="Query text (positional, or use --query)")
    q.add_argument("--query", dest="query_text", default=None,
                   metavar="TEXT",
                   help="Query text (alternative to positional argument)")
    q.add_argument("--top-k", type=int, default=5)
    q.add_argument("--file", dest="filter_file", default=None,
                   help="Filter results to entries touching this file (substring match)")
    q.add_argument("--function", dest="filter_function", default=None,
                   help="Filter results to entries touching this function")
    q.add_argument("--format", default="concise", choices=["concise", "full"],
                   help="Output format: concise (decisions+summary) or full JSON")
    q.set_defaults(func=cmd_query_memory)

    s = sub.add_parser("state", help="Show engine state summary")
    s.set_defaults(func=cmd_state)

    us = sub.add_parser("update_status", help="Update an entry's status")
    us.add_argument("--id", required=True)
    us.add_argument("--status", required=True, choices=["active", "resolved", "conflict", "superseded"])
    us.add_argument("--reason", default="")
    us.set_defaults(func=cmd_update_status)

    lc = sub.add_parser(
        "list_conflicts",
        help="List all unresolved conflicts with full entry details",
    )
    lc.set_defaults(func=cmd_list_conflicts)

    rc = sub.add_parser(
        "resolve_conflict",
        help="Resolve a conflict: supersede_a, supersede_b, merge, or dismiss",
    )
    rc.add_argument("--id", required=True, metavar="CONFLICT_ID",
                    help="Conflict record id (from list_conflicts)")
    rc.add_argument(
        "--action", required=True,
        choices=["supersede_a", "supersede_b", "merge", "dismiss"],
        help=(
            "supersede_a: keep entry_b, mark entry_a superseded. "
            "supersede_b: keep entry_a, mark entry_b superseded. "
            "merge: create combined entry, supersede both. "
            "dismiss: remove conflict record, keep both entries unchanged."
        ),
    )
    rc.add_argument("--reason", default="",
                    help="Human-readable reason stored in the activity log")
    rc.set_defaults(func=cmd_resolve_conflict)

    uc = sub.add_parser("update_confidence", help="Update an entry's confidence")
    uc.add_argument("--id", required=True)
    uc.add_argument("--confidence", type=float, required=True)
    uc.add_argument("--reason", default="")
    uc.set_defaults(func=cmd_update_confidence)

    dc = sub.add_parser(
        "decay",
        help="Apply time-based confidence decay to stale entries",
    )
    dc.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="Compute decay changes but do NOT write to disk",
    )
    dc.add_argument(
        "--half-life-days", dest="half_life_days", type=float, default=60.0,
        help="Days until confidence halves (default: 60)",
    )
    dc.add_argument(
        "--min-confidence", dest="min_confidence", type=float, default=0.40,
        help="Confidence floor: entries never decay below this (default: 0.40)",
    )
    dc.set_defaults(func=cmd_decay)

    dp = sub.add_parser(
        "decay_preview",
        help="Preview effective (decayed) confidence for all entries (read-only)",
    )
    dp.add_argument(
        "--all", dest="all", action="store_true", default=False,
        help="Show all entries, not just those with changed confidence",
    )
    dp.set_defaults(func=cmd_decay_preview)

    dd = sub.add_parser(
        "deduplicate",
        help="Find and merge near-duplicate entries into single canonical entries",
    )
    dd.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="Show clusters that would be merged without writing to disk",
    )
    dd.add_argument(
        "--threshold", type=float, default=0.88,
        help="Cosine similarity threshold for deduplication (default: 0.88)",
    )
    dd.set_defaults(func=cmd_deduplicate)

    fd = sub.add_parser(
        "find_duplicates",
        help="List near-duplicate entry clusters (read-only, alias for deduplicate --dry-run)",
    )
    fd.add_argument(
        "--threshold", type=float, default=0.88,
        help="Cosine similarity threshold (default: 0.88)",
    )
    fd.set_defaults(func=cmd_find_duplicates)

    rw = sub.add_parser("render_wiki", help="Render markdown wiki under data/wiki/")
    rw.set_defaults(func=cmd_render_wiki)

    al = sub.add_parser(
        "add_link",
        help="Create a dependency link: from_id depends_on to_id",
    )
    al.add_argument("--from", dest="from_id", required=True, metavar="FROM_ID",
                    help="Entry ID that depends on the target")
    al.add_argument("--to", dest="to_id", required=True, metavar="TO_ID",
                    help="Entry ID being depended upon")
    al.set_defaults(func=cmd_add_link)

    rl = sub.add_parser(
        "remove_link",
        help="Remove a dependency link between two entries",
    )
    rl.add_argument("--from", dest="from_id", required=True, metavar="FROM_ID")
    rl.add_argument("--to", dest="to_id", required=True, metavar="TO_ID")
    rl.set_defaults(func=cmd_remove_link)

    gd = sub.add_parser(
        "get_dependencies",
        help="Show the dependency subgraph for an entry",
    )
    gd.add_argument("--id", required=True, metavar="ENTRY_ID")
    gd.add_argument("--depth", type=int, default=1,
                    help="Traversal depth (default: 1; -1 for full transitive)")
    gd.set_defaults(func=cmd_get_dependencies)

    sl = sub.add_parser(
        "suggest_links",
        help="Suggest depends_on links for an entry based on semantic similarity",
    )
    sl.add_argument("--id", required=True, metavar="ENTRY_ID")
    sl.add_argument("--threshold", type=float, default=0.75,
                    help="Minimum similarity score (default: 0.75)")
    sl.add_argument("--top-k", dest="top_k", type=int, default=5,
                    help="Maximum number of suggestions to return (default: 5)")
    sl.set_defaults(func=cmd_suggest_links)

    ui = sub.add_parser(
        "update_instructions",
        help="Generate Learned Patterns from decisions and update copilot-instructions.md",
    )
    ui.add_argument(
        "--project-path", dest="project_path", required=True,
        help="Path to the project root (must contain .github/copilot-instructions.md)",
    )
    ui.add_argument(
        "--min-confidence", dest="min_confidence", type=float, default=0.8,
        help="Minimum confidence to include (default: 0.8)",
    )
    ui.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="Print content without writing to disk",
    )
    ui.set_defaults(func=cmd_update_instructions)

    ln = sub.add_parser("lint", help="Health-check the memory store")
    ln.add_argument("--stale-days", type=int, default=180,
                    help="Active entries older than this are flagged stale (default: 180)")
    ln.add_argument("--low-confidence", type=float, default=0.3,
                    help="Confidence below this is flagged (default: 0.3)")
    ln.set_defaults(func=cmd_lint)

    sf = sub.add_parser(
        "summarize_file",
        help="Generate (or refresh) the auto-summary for a source file",
    )
    sf.add_argument(
        "--file", required=True, metavar="FILE_PATH",
        help="File path as recorded in memory entries (e.g. src/main.cpp)",
    )
    sf.set_defaults(func=cmd_summarize_file)

    cs = sub.add_parser(
        "check_stale",
        help="Detect memory entries whose referenced files/functions no longer exist in the repo",
    )
    cs.add_argument(
        "--repo-path", dest="repo_path", required=True, metavar="PATH",
        help="Absolute path to the git repository root",
    )
    cs.add_argument(
        "--min-age-days", dest="min_age_days", type=int, default=7,
        help="Only check entries older than this many days (default: 7)",
    )
    cs.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="List stale candidates without tagging them",
    )
    cs.add_argument(
        "--apply", dest="dry_run", action="store_false",
        help="Tag stale entries and write to disk (default when --dry-run is omitted)",
    )
    cs.set_defaults(func=cmd_check_stale)

    su = sub.add_parser(
        "stabilize_unstable",
        help="Remove 'unstable' tag from entries with no revert activity for N days",
    )
    su.add_argument(
        "--min-stable-days", dest="min_stable_days", type=int, default=14,
        help="Days without revert activity required to consider an entry stable (default: 14)",
    )
    su.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="Show which entries would be stabilised without writing to disk",
    )
    su.add_argument(
        "--apply", dest="dry_run", action="store_false",
        help="Write changes to disk (default when --dry-run is omitted)",
    )
    su.set_defaults(func=cmd_stabilize_unstable)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
