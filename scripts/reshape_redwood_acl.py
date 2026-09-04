#!/usr/bin/env python3
"""Give the Redwood corpus permission-set shapes beyond its original four.

The native corpus resolves to exactly four document ACL signatures: company-wide
plus three single-group grants. Multi-group unions, direct user grants, and
grants combining both go unexercised by real retrieval, and `permission_set_users`
stays empty -- which is why bulk_verify reports its direct-user checks as
"not_applicable" rather than running them.

This rewrites a handful of company-wide Confluence pages into richer shapes.
Constraints it respects, each read out of dataset.py:

- Groups exist only through employee membership (dataset.py:250), so a grant to
  a memberless group cannot be expressed in this corpus at all.
  `group-private-deployments`, with three members, is the closest real analogue.
- Documents named by the golden set are never touched. Restricting one a query
  expects would move recall without any test saying so.
- Every signature lands on more than one document, so each proves a single
  permission set can govern several documents.
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
SOURCE = "artifacts/confluence.jsonl"
PER_SIGNATURE = 2


def signatures(members: dict[str, list[str]]) -> list[tuple[str, dict]]:
    """Return (label, acl) pairs, drawing user ids from real group membership."""
    security = sorted(members["group-security"])
    platform = sorted(members["group-platform"])
    outsiders = sorted(
        set(members["group-all-employees"]) - set(platform) - set(security)
    )

    def acl(groups=(), users=()):
        return {
            "company_access": False,
            "group_ids": list(groups),
            "user_ids": list(users),
        }

    return [
        ("two-group union", acl(groups=["group-security", "group-platform"])),
        ("three-group union",
         acl(groups=["group-product", "group-revenue", "group-workplace"])),
        ("rare group", acl(groups=["group-private-deployments"])),
        ("direct grant only", acl(users=outsiders[:3])),
        ("single direct grant", acl(users=outsiders[3:4])),
        ("group plus outside direct grant",
         acl(groups=["group-infrastructure"], users=outsiders[4:6])),
        ("group plus redundant direct grant",
         acl(groups=["group-security"], users=security[:1])),
        ("workplace and security union",
         acl(groups=["group-workplace", "group-security"])),
    ]


def select(
    records: list[dict],
    reserved: set[str],
    plan: list[tuple[str, dict]],
    per_signature: int = PER_SIGNATURE,
) -> list[tuple[int, dict]]:
    """Return (record index, target acl) pairs, the same ones on every run."""
    targets = [acl for _, acl in plan]

    def eligible(record: dict) -> bool:
        acl = record["acl"]
        if record["id"] in reserved:
            return False
        # A document this script already rewrote stays eligible. Drop that and
        # the pool shrinks under our own edits, so a second run picks a fresh
        # set of documents and restricts those too.
        plain = acl["company_access"] and not acl["group_ids"] and not acl["user_ids"]
        return plain or acl in targets

    candidates = [index for index, record in enumerate(records) if eligible(record)]
    needed = len(plan) * per_signature
    if len(candidates) < needed:
        raise SystemExit(f"only {len(candidates)} candidates, need {needed}")

    # Spread the picks over the whole file. Confluence records cluster by space,
    # so consecutive picks would restrict one topic and let a single query
    # collide with several restricted documents at once.
    spread = candidates[:: len(candidates) // needed][:needed]
    return [
        (spread[position * per_signature + offset], acl)
        for position, (_, acl) in enumerate(plan)
        for offset in range(per_signature)
    ]


def rewrite(
    source: Path,
    reserved: set[str],
    plan: list[tuple[str, dict]],
    per_signature: int = PER_SIGNATURE,
    apply: bool = False,
) -> int:
    """Apply the plan to one JSONL file, leaving untouched lines byte-identical."""
    with open(source, encoding="utf-8", newline="") as handle:
        lines = handle.read().splitlines()
    records = [json.loads(line) for line in lines]

    changed = 0
    for index, acl in select(records, reserved, plan, per_signature):
        if records[index]["acl"] == acl:
            continue
        records[index]["acl"] = dict(acl)
        lines[index] = json.dumps(records[index], sort_keys=True, ensure_ascii=False)
        changed += 1

    if changed and apply:
        with open(source, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
    return changed


def golden_document_ids(golden: Path) -> set[str]:
    reserved: set[str] = set()
    for query in json.loads(golden.read_text(encoding="utf-8")):
        reserved.update((query.get("relevant") or {}).keys())
        reserved.update(query.get("must_not_appear") or [])
    return reserved


def group_members(employees: Path) -> dict[str, list[str]]:
    members: dict[str, list[str]] = {}
    for line in employees.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        for group_id in record["group_ids"]:
            members.setdefault(group_id, []).append(record["id"])
    return members


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "redwood")
    parser.add_argument(
        "--golden", type=Path, default=ROOT / "eval" / "redwood_queries.json"
    )
    parser.add_argument("--apply", action="store_true", help="write the file")
    args = parser.parse_args()

    plan = signatures(group_members(args.data / "employees.jsonl"))
    changed = rewrite(
        args.data / SOURCE,
        golden_document_ids(args.golden),
        plan,
        apply=args.apply,
    )
    print(f"{changed} documents rewritten in {SOURCE}")
    if changed and not args.apply:
        print("dry run; pass --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
