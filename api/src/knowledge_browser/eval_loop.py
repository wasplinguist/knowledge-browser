"""Deterministic mechanics for behavior-led search experiments."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import time
from typing import Any

from .eval_entitlement import audit_acl, entitlement_snapshot
from .evaluation import compare_runs, evaluate_queries, load_golden_queries
from .profiles import load_profile
from .repository import resolve_identity
from .search import hybrid_search


REQUIRED = (
    "id", "created_at", "evidence_report", "insight", "hypothesis",
    "implementation", "affected_intents", "target_metrics", "regression_risk",
    "intent_audit", "baseline_profile", "challenger_profile", "golden_queries",
    "query_embeddings", "golden_changes", "golden_change_reason", "status",
)
PATH_FIELDS = (
    "evidence_report", "baseline_profile", "challenger_profile",
    "golden_queries", "query_embeddings",
)


def _path(root: Path, value: str, *, external_allowed: bool = False) -> Path:
    candidate = Path(value)
    result = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not external_allowed and not result.is_relative_to(root.resolve()):
        raise ValueError("experiment paths must stay inside the repository")
    return result


def experiment_paths(manifest: Mapping[str, Any], root: Path) -> dict[str, Path]:
    return {
        field: _path(
            root, manifest[field],
            external_allowed=field in {"evidence_report", "query_embeddings"},
        )
        for field in PATH_FIELDS
    }


def _timestamp(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_manifest(
    path: Path,
    root: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("experiment manifest must stay inside the repository")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    missing = [field for field in REQUIRED if field not in manifest]
    if missing:
        raise ValueError("missing experiment fields: " + ", ".join(missing))
    if manifest["status"] != "implemented":
        raise ValueError("experiment status must be implemented")
    if manifest["intent_audit"].get("verdict") != "ALIGNED":
        raise ValueError("intent audit must be ALIGNED")
    if not manifest["affected_intents"] or not manifest["target_metrics"]:
        raise ValueError("affected intents and target metrics are required")
    paths = experiment_paths(manifest, root)
    for field, item in paths.items():
        if not item.is_file():
            raise ValueError(f"{field} does not exist")
    expected_baseline = (root / "search" / "profiles" / "released.json").resolve()
    if paths["baseline_profile"] != expected_baseline:
        raise ValueError("baseline must be the repository released profile")
    if paths["baseline_profile"] == paths["challenger_profile"]:
        raise ValueError("baseline and challenger profiles must differ")
    baseline_profile = load_profile(paths["baseline_profile"])
    challenger_profile = load_profile(paths["challenger_profile"])
    if baseline_profile == replace(challenger_profile, name=baseline_profile.name):
        raise ValueError("challenger behavior settings must differ from released")

    evidence = json.loads(paths["evidence_report"].read_text(encoding="utf-8"))
    if not isinstance(evidence, dict):
        raise ValueError("evidence report must be a weekly report object")
    try:
        since = _timestamp(evidence["since"], "evidence since")
        until = _timestamp(evidence["until"], "evidence until")
        created_at = _timestamp(manifest["created_at"], "experiment created_at")
        total_searches = evidence["total_searches"]
        excluded_profiles = evidence["excluded_profiles"]
    except KeyError as error:
        raise ValueError("evidence report fields are incomplete") from error
    current = now().astimezone(timezone.utc)
    if not since < until or not until <= created_at <= current:
        raise ValueError("evidence and experiment timestamps are invalid")
    if current - until > timedelta(days=1):
        raise ValueError("evidence report is not fresh")
    if not isinstance(total_searches, int) or total_searches < 1:
        raise ValueError("evidence report has no useful behavior")
    if (
        not isinstance(excluded_profiles, list)
        or "demo-loop-v1" not in excluded_profiles
    ):
        raise ValueError("evidence report must record excluded synthetic profiles")
    queries = load_golden_queries(paths["golden_queries"])
    embeddings = json.loads(paths["query_embeddings"].read_text(encoding="utf-8"))
    missing_embeddings = [item["id"] for item in queries if item["id"] not in embeddings]
    if missing_embeddings:
        raise ValueError("query embeddings are incomplete")
    for query in queries:
        vector = embeddings[query["id"]]
        if (
            not isinstance(vector, list)
            or len(vector) != 1536
            or any(not isinstance(value, (int, float)) for value in vector)
        ):
            raise ValueError("every query embedding must contain 1,536 numbers")
    embeddings_by_text: dict[str, list[float]] = {}
    for query in queries:
        vector = embeddings[query["id"]]
        previous = embeddings_by_text.setdefault(query["query"], vector)
        if previous != vector:
            raise ValueError("queries with the same text must use the same embedding")
    return manifest


def select_fast_acl_inputs(
    queries: Sequence[Mapping[str, Any]], users: Sequence[str], user_limit: int = 12
) -> tuple[list[Mapping[str, Any]], list[str]]:
    selected: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        if query.get("acl_aware"):
            selected.append(query)
            seen.add(query["id"])
    type_counts: dict[str, int] = {}
    for query in queries:
        kind = str(query.get("type", "unknown"))
        if query["id"] not in seen and type_counts.get(kind, 0) < 2:
            selected.append(query)
            seen.add(query["id"])
            type_counts[kind] = type_counts.get(kind, 0) + 1
    owners = {str(item["as_user"]) for item in selected}
    ordered_users = sorted(set(users))
    if len(ordered_users) <= user_limit:
        selected_users = ordered_users
    elif user_limit == 1:
        selected_users = [ordered_users[0]]
    else:
        selected_users = [
            ordered_users[round(index * (len(ordered_users) - 1) / (user_limit - 1))]
            for index in range(user_limit)
        ]
    selected_users = sorted(set(selected_users).union(owners))
    return selected, selected_users


def decide(evaluation: Mapping[str, Any]) -> str:
    comparison = evaluation["comparison"]
    candidate = evaluation["candidate"]["overall"]
    acl = evaluation["fast_acl"]
    safe = (
        candidate.get("forbidden_leaks", 0) == 0
        and not acl.get("root_leaks")
        and not acl.get("child_leaks")
    )
    quality = (
        comparison["overall_delta"]["ndcg@10"] >= 0.01
        and comparison["overall_delta"]["recall@10"] >= 0
        and len(comparison["losses"]) <= len(comparison["wins"])
    )
    latency = evaluation["latency_ms"]
    acceptable_latency = latency["candidate"] <= max(
        latency["baseline"] * 1.2,
        latency["baseline"] + 250,
    )
    return "recommend-release-gate" if safe and quality and acceptable_latency else "reject"


def execute_evaluation(
    conn, manifest: Mapping[str, Any], paths: Mapping[str, Path]
) -> dict[str, Any]:
    queries = load_golden_queries(paths["golden_queries"])
    embeddings = json.loads(paths["query_embeddings"].read_text(encoding="utf-8"))
    baseline_profile = load_profile(paths["baseline_profile"])
    candidate_profile = load_profile(paths["challenger_profile"])

    identities: dict[str, Any] = {}
    embedding_by_text = {
        item["query"]: embeddings[item["id"]]
        for item in queries
    }

    def identity(value: str):
        if value not in identities:
            identities[value] = resolve_identity(conn, value)
        return identities[value]

    def run(profile):
        started = time.perf_counter()

        def search(user, query, _profile_name):
            resolved = identity(str(user))
            if resolved is None:
                return []
            return hybrid_search(
                conn, resolved.id, query, embedding_by_text[query], profile=profile
            )

        result = evaluate_queries(queries, search, profile.name)
        return result, max(1, int((time.perf_counter() - started) * 1000))

    baseline, baseline_ms = run(baseline_profile)
    candidate, candidate_ms = run(candidate_profile)
    comparison = compare_runs(baseline, candidate)

    memberships, documents = entitlement_snapshot(conn)
    selected_queries, selected_users = select_fast_acl_inputs(
        queries, [str(user_id) for user_id in memberships]
    )
    selected_memberships = {}
    for user in selected_users:
        resolved = identity(user)
        if resolved is None or resolved.id not in memberships:
            raise ValueError(f"fast ACL sample user cannot be resolved: {user}")
        selected_memberships[resolved.id] = memberships[resolved.id]
    fast_acl = audit_acl(
        selected_memberships,
        documents,
        [item["query"] for item in selected_queries],
        lambda user, query: hybrid_search(
            conn, user, query, embedding_by_text[query], profile=candidate_profile
        ),
    )
    return {
        "baseline": baseline,
        "candidate": candidate,
        "comparison": comparison,
        "fast_acl": fast_acl,
        "latency_ms": {"baseline": baseline_ms, "candidate": candidate_ms},
    }


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _report(manifest: Mapping[str, Any], run: Mapping[str, Any]) -> str:
    baseline = run["baseline"]["overall"]
    candidate = run["candidate"]["overall"]
    comparison = run["comparison"]
    acl = run["fast_acl"]
    latency = run["latency_ms"]
    hashes = run["provenance"]["sha256"]
    wins = ", ".join(comparison["wins"]) or "None"
    losses = ", ".join(comparison["losses"]) or "None"
    hash_rows = "".join(
        f"<li>{escape(name)}: <code>{escape(value)}</code></li>"
        for name, value in hashes.items()
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Search experiment</title><style>body{{font:16px/1.5 system-ui;max-width:850px;margin:40px auto;padding:0 20px}}.card{{border:1px solid #ddd;border-radius:12px;padding:16px;margin:14px 0}}</style></head><body>
<h1>Search experiment</h1>
<h2>Behavior → idea</h2><div class="card"><p><b>Evidence:</b> {escape(manifest['evidence_report'])}</p><p>{escape(manifest['insight'])}</p><p><b>Hypothesis:</b> {escape(manifest['hypothesis'])}</p><p><b>Change:</b> {escape(manifest['implementation'])}</p></div>
<h2>Fresh comparison</h2><div class="card"><p>nDCG@10: {baseline['ndcg@10']:.3f} → {candidate['ndcg@10']:.3f}</p><p>Recall@10: {baseline['recall@10']:.3f} → {candidate['recall@10']:.3f}</p><p>Wins ({len(comparison['wins'])}): {escape(wins)}</p><p>Losses ({len(comparison['losses'])}): {escape(losses)}</p></div>
<h2>Fast ACL sample</h2><div class="card"><p>{acl['pairs']} query/user pairs</p><p>Root leaks: {len(acl['root_leaks'])} · Child leaks: {len(acl['child_leaks'])}</p><p>This is not the native full ACL release gate.</p></div>
<h2>Latency</h2><div class="card"><p>Baseline: {latency['baseline']} ms · Challenger: {latency['candidate']} ms</p></div>
<h2>Input hashes</h2><div class="card"><ul>{hash_rows}</ul></div>
<h2>Decision</h2><div class="card"><b>{run['decision']}</b><p>No profile was promoted automatically.</p></div>
</body></html>"""


def run_experiment(
    manifest_path: Path,
    output_dir: Path,
    *,
    root: Path,
    evaluate: Callable[[Mapping[str, Any], Mapping[str, Path]], dict[str, Any]],
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    git_sha: str = "unknown",
    command: Sequence[str] = (),
) -> Path:
    if output_dir.exists():
        raise ValueError("output directory must not already exist")
    started_at = now().astimezone(timezone.utc)
    manifest = validate_manifest(manifest_path, root, now=lambda: started_at)
    paths = experiment_paths(manifest, root)
    input_paths = {"manifest": manifest_path, **paths}
    input_hashes = {name: _hash(item) for name, item in input_paths.items()}
    evaluation = evaluate(manifest, paths)
    if input_hashes != {name: _hash(item) for name, item in input_paths.items()}:
        raise ValueError("experiment inputs changed during evaluation")
    run = {
        **evaluation,
        "experiment_id": manifest["id"],
        "created_at": started_at.isoformat(),
        "decision": decide(evaluation),
        "provenance": {
            "git_sha": git_sha,
            "command": list(command),
            "sha256": input_hashes,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "run.json").write_text(
        json.dumps(run, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report = output_dir / "report.html"
    report.write_text(_report(manifest, run), encoding="utf-8")
    return report
