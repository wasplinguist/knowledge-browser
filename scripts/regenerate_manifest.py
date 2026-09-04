#!/usr/bin/env python3
"""Recompute the dataset manifest after an intentional edit to data/redwood.

Hashes and counts come from the same helpers `validate_manifest` uses, so the
two can never disagree about what a file's digest or a record count is. The
manifest is written with LF endings: a single CRLF invalidates every hash.

Unknown top-level keys (dataset_version, seed) are preserved as they are.
"""

import argparse
import json
from pathlib import Path
import sys

from knowledge_browser.dataset import (
    COUNT_KEYS,
    REQUIRED_FILES,
    SOURCES,
    _iter_jsonl,
    _stream_sha256,
)

ROOT = Path(__file__).parents[1]


def _counts(root: Path) -> dict[str, int]:
    counts = {
        "artifacts": sum(
            sum(1 for _ in _iter_jsonl(root / "artifacts" / f"{source}.jsonl"))
            for source in SOURCES
        ),
        "companies": 1,
        "employees": sum(1 for _ in _iter_jsonl(root / "employees.jsonl")),
        "incidents": sum(1 for _ in _iter_jsonl(root / "events.jsonl")),
        "projects": sum(1 for _ in _iter_jsonl(root / "projects.jsonl")),
        "qa": sum(1 for _ in _iter_jsonl(root / "qa.jsonl")),
        "teams": sum(1 for _ in _iter_jsonl(root / "teams.jsonl")),
    }
    missing = set(COUNT_KEYS).difference(counts)
    if missing:
        raise SystemExit(f"manifest count not computed: {sorted(missing)[0]}")
    return counts


def build_manifest(root: Path) -> dict:
    """Return the manifest the current dataset files imply."""
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            raise SystemExit(f"missing dataset file: {relative}")
    manifest["counts"] = _counts(root)
    manifest["files"] = {
        relative: _stream_sha256(root / relative)
        for relative in sorted(REQUIRED_FILES)
    }
    return manifest


def render(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "redwood")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the manifest is already out of date, write nothing",
    )
    args = parser.parse_args()

    root = args.data
    target = root / "manifest.json"
    rendered = render(build_manifest(root))
    with open(target, encoding="utf-8", newline="") as handle:
        current = handle.read()

    if rendered == current:
        print(f"manifest already matches {root}")
        return 0
    if args.check:
        print(f"manifest is stale: {target}", file=sys.stderr)
        return 1

    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)
    print(f"rewrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
