"""CLI for VS Code + GitHub Copilot infrastructure management.

Commands
--------
  setup   — scaffold Copilot infrastructure inside a project folder
  list    — list all managed (registered) projects
  scan    — scan VS Code debug logs for new Copilot activity (single pass)
  watch   — tail VS Code debug logs continuously
  learn   — analyse activity and update managed project instructions
  status  — show a quick system summary
  recent  — print the most recent recorded Copilot events

Usage examples
--------------
  python run_infra.py setup C:\\Projects\\MyApp --language python --framework django
  python run_infra.py scan
  python run_infra.py watch --interval 10
  python run_infra.py learn
  python run_infra.py status
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"


# ── pretty-print helper ───────────────────────────────────────────────────────

def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


# ── command handlers ──────────────────────────────────────────────────────────

def cmd_setup(args: argparse.Namespace) -> int:
    from core.vscode_infra import VSCodeInfraBuilder

    builder = VSCodeInfraBuilder(DATA_DIR)
    result = builder.create_in(
        args.path,
        project_name=args.name,
        language=args.language,
        framework=args.framework,
        extra_instructions=args.instructions,
    )
    _print(result)
    print(
        f"\n[setup] Infrastructure created/merged in: {result['project']}\n"
        f"        Files created : {result['created']}\n"
        f"        Files merged  : {result['skipped_merged']}"
    )
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    from core.vscode_infra import VSCodeInfraBuilder

    builder = VSCodeInfraBuilder(DATA_DIR)
    projects = builder.list_managed()
    if not projects:
        print("[list] No managed projects yet. Run `setup` to register one.")
        return 0
    _print(projects)
    return 0


def cmd_scan(_: argparse.Namespace) -> int:
    from core.copilot_logger import CopilotActivityLogger

    logger = CopilotActivityLogger(DATA_DIR)
    count = logger.scan_once()
    stats = logger.stats()
    print(
        f"[scan] New events recorded : {count}\n"
        f"[scan] Total stored events : {stats['total_events']}\n"
        f"[scan] Log dirs discovered : {stats['log_dirs_found']}\n"
        f"[scan] Activity file       : {stats['activity_file']}"
    )
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from core.copilot_logger import CopilotActivityLogger

    logger = CopilotActivityLogger(DATA_DIR)
    logger.watch(interval_seconds=args.interval)
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    from core.learner import CopilotLearner

    learner = CopilotLearner(DATA_DIR)
    result = learner.update_all_managed(dry_run=args.dry_run)
    _print(result)

    if not args.skip_self_update and not args.dry_run:
        self_result = learner.self_update_templates()
        print("\n[learn] Template self-update result:")
        _print(self_result)
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    from core.vscode_infra import VSCodeInfraBuilder
    from core.copilot_logger import CopilotActivityLogger

    builder = VSCodeInfraBuilder(DATA_DIR)
    logger = CopilotActivityLogger(DATA_DIR)

    _print({
        "managed_projects": len(builder.list_managed()),
        "copilot_activity": logger.stats(),
        "recent_5_events": logger.recent(5),
    })
    return 0


def cmd_recent(args: argparse.Namespace) -> int:
    from core.copilot_logger import CopilotActivityLogger

    logger = CopilotActivityLogger(DATA_DIR)
    events = logger.recent(args.n)
    if not events:
        print("[recent] No activity recorded yet. Run `scan` first.")
        return 0
    _print(events)
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    from core.watcher import Daemon
    from pathlib import Path as _Path

    project_paths = None
    if args.projects:
        project_paths = [_Path(p).resolve() for p in args.projects]

    daemon = Daemon(
        data_dir=DATA_DIR,
        project_paths=project_paths,
        log_interval=args.log_interval,
    )
    daemon.start(block=True)
    return 0


# ── argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_infra",
        description="VS Code + GitHub Copilot infrastructure manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="command", required=True)

    # ── setup ──────────────────────────────────────────────────────────────
    s = sub.add_parser(
        "setup",
        help="Create Copilot infrastructure in a project folder",
    )
    s.add_argument("path", help="Absolute or relative path to the project folder")
    s.add_argument("--name", default=None, help="Project display name (default: folder name)")
    s.add_argument("--language", default=None, help="Primary programming language")
    s.add_argument("--framework", default=None, help="Framework / platform")
    s.add_argument("--instructions", default=None, help="Extra free-text instructions")
    s.set_defaults(func=cmd_setup)

    # ── list ───────────────────────────────────────────────────────────────
    li = sub.add_parser("list", help="List all managed projects")
    li.set_defaults(func=cmd_list)

    # ── scan ───────────────────────────────────────────────────────────────
    sc = sub.add_parser("scan", help="One-shot scan of VS Code Copilot debug logs")
    sc.set_defaults(func=cmd_scan)

    # ── watch ──────────────────────────────────────────────────────────────
    wa = sub.add_parser("watch", help="Continuously tail Copilot debug logs")
    wa.add_argument(
        "--interval", type=float, default=5.0,
        help="Seconds between scans (default: 5)",
    )
    wa.set_defaults(func=cmd_watch)

    # ── learn ──────────────────────────────────────────────────────────────
    le = sub.add_parser(
        "learn",
        help="Analyse activity and push learned context to managed projects",
    )
    le.add_argument(
        "--dry-run", action="store_true",
        help="Compute analysis without writing any files",
    )
    le.add_argument(
        "--skip-self-update", action="store_true",
        help="Do not update master template files",
    )
    le.set_defaults(func=cmd_learn)

    # ── status ─────────────────────────────────────────────────────────────
    st = sub.add_parser("status", help="Show system status summary")
    st.set_defaults(func=cmd_status)

    # ── recent ─────────────────────────────────────────────────────────────
    re_ = sub.add_parser("recent", help="Show most recent recorded Copilot events")
    re_.add_argument("--n", type=int, default=20, help="Number of events to show (default: 20)")
    re_.set_defaults(func=cmd_recent)

    # ── daemon ─────────────────────────────────────────────────────────────
    da = sub.add_parser(
        "daemon",
        help="Background watcher: auto-create memory entries when files change",
    )
    da.add_argument(
        "--projects", nargs="*", default=None,
        metavar="PATH",
        help="Project paths to watch (default: all managed projects)",
    )
    da.add_argument(
        "--log-interval", type=float, default=15.0,
        help="Seconds between Copilot log scans (default: 15)",
    )
    da.set_defaults(func=cmd_daemon)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
