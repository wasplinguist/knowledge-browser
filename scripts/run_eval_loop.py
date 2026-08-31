#!/usr/bin/env python3
"""Run the deterministic parts of the eval-driven development loop."""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import subprocess
import sys

from knowledge_browser.db import connection
from knowledge_browser.eval_loop import execute_evaluation, run_experiment
from knowledge_browser.weekly import build_weekly_report, write_weekly_report


ROOT = Path(__file__).parents[1]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout


def _repository_roots() -> tuple[Path, ...]:
    common_value = Path(_git("rev-parse", "--git-common-dir").strip())
    common = (common_value if common_value.is_absolute() else ROOT / common_value).resolve()
    worktrees = [
        Path(line.removeprefix("worktree ")).resolve()
        for line in _git("worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]
    return tuple(dict.fromkeys([common, *worktrees]))


def _outside_repo(path: Path) -> Path:
    resolved = path.resolve()
    if any(resolved == root or resolved.is_relative_to(root) for root in _repository_roots()):
        raise ValueError("generated output must stay outside every Git worktree and .git")
    return resolved


def _source_state() -> str:
    if _git("status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("evaluation requires a clean committed worktree")
    digest = sha256()
    tracked = _git("ls-files", "-z").split("\0")
    for value in (item for item in tracked if item):
        path = ROOT / value
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def analyze(days: int, output_dir: Path, excluded_profiles: tuple[str, ...]) -> Path:
    until = datetime.now(timezone.utc)
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        report = build_weekly_report(
            conn, until - timedelta(days=days), until, excluded_profiles
        )
    return write_weekly_report(report, _outside_repo(output_dir))[0]


def evaluate(experiment: Path, output_dir: Path) -> Path:
    git_sha = _git("rev-parse", "HEAD").strip()
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return run_experiment(
            experiment.resolve(),
            _outside_repo(output_dir),
            root=ROOT,
            evaluate=lambda manifest, paths: execute_evaluation(conn, manifest, paths),
            git_sha=git_sha,
            command=sys.argv,
            source_state=_source_state,
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
