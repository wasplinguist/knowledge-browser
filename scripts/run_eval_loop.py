#!/usr/bin/env python3
"""Run the deterministic parts of the eval-driven development loop."""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys

from knowledge_browser.db import connection
from knowledge_browser.eval_loop import execute_evaluation, run_experiment
from knowledge_browser.weekly import build_weekly_report, write_weekly_report


ROOT = Path(__file__).parents[1]


def _outside_repo(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.is_relative_to(ROOT.resolve()):
        raise ValueError("generated output must stay outside the repository")
    return resolved


def analyze(days: int, output_dir: Path, excluded_profiles: tuple[str, ...]) -> Path:
    until = datetime.now(timezone.utc)
    with connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        report = build_weekly_report(
            conn, until - timedelta(days=days), until, excluded_profiles
        )
    return write_weekly_report(report, _outside_repo(output_dir))[0]


def evaluate(experiment: Path, output_dir: Path) -> Path:
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    with connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        return run_experiment(
            experiment.resolve(),
            _outside_repo(output_dir),
            root=ROOT,
            evaluate=lambda manifest, paths: execute_evaluation(conn, manifest, paths),
            git_sha=git_sha,
            command=sys.argv,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a fresh search experiment loop")
    commands = parser.add_subparsers(dest="command", required=True)
    analyze_parser = commands.add_parser("analyze", help="write fresh behavior evidence")
    analyze_parser.add_argument("--days", type=int, default=7)
    analyze_parser.add_argument("--output-dir", type=Path, required=True)
    analyze_parser.add_argument(
        "--exclude-profile", action="append", default=["demo-loop-v1"]
    )
    evaluate_parser = commands.add_parser("evaluate", help="run a fresh comparison")
    evaluate_parser.add_argument("--experiment", type=Path, required=True)
    evaluate_parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "analyze":
            if args.days < 1:
                raise ValueError("--days must be positive")
            result = analyze(args.days, args.output_dir, tuple(args.exclude_profile))
        else:
            result = evaluate(args.experiment, args.output_dir)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
