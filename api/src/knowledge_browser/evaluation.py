"""Small, deterministic search and RAG evaluation helpers."""

from collections.abc import Callable, Iterable, Mapping, Sequence
import json
import math
from pathlib import Path
from typing import Any


def recall_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    expected = set(relevant)
    return len(expected.intersection(ranked[:k])) / len(expected) if expected else 0.0


def reciprocal_rank(
    ranked: Sequence[str], relevant: Iterable[str], k: int | None = None
) -> float:
    expected = set(relevant)
    for rank, value in enumerate(ranked if k is None else ranked[:k], start=1):
        if value in expected:
            return 1 / rank
    return 0.0


def ndcg_at_k(ranked: Sequence[str], grades: Mapping[str, float], k: int) -> float:
    def dcg(gains: Iterable[float]) -> float:
        return sum(
            gain / math.log2(rank + 1)
            for rank, gain in enumerate(gains, start=1)
        )

    if not grades:
        return 0.0
    actual = dcg(2 ** grades.get(value, 0) - 1 for value in ranked[:k])
    ideal = dcg(sorted((2 ** value - 1 for value in grades.values()), reverse=True)[:k])
    return actual / ideal if ideal else 0.0


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def load_golden_queries(path: Path) -> list[dict[str, Any]]:
    queries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(queries, list) or not queries:
        raise ValueError("golden queries must be a non-empty list")
    ids: list[str] = []
    for query in queries:
        if not isinstance(query, dict):
            raise ValueError("every golden query must be an object")
        required = ("id", "as_user", "query", "relevant")
        if any(not query.get(field) for field in required[:3]) or not isinstance(
            query.get("relevant"), list
        ):
            raise ValueError("golden query fields are invalid")
        ids.append(query["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("golden query IDs must be unique")
    return queries


def evaluate_queries(
    queries: Sequence[Mapping[str, Any]],
    search: Callable[[str, str, str], Sequence[Mapping[str, Any]]],
    profile: str,
) -> dict[str, Any]:
    per_query: list[dict[str, Any]] = []
    for query in queries:
        ranked = list(dict.fromkeys(
            str(item["external_id"])
            for item in search(query["as_user"], query["query"], profile)
        ))
        relevant = set(query.get("relevant", []))
        grades = query.get("grades") or {value: 1 for value in relevant}
        forbidden = sorted(set(ranked).intersection(query.get("must_not_appear", [])))
        per_query.append({
            "id": query["id"],
            "ranked": ranked,
            "forbidden": forbidden,
            "metrics": {
                "mrr@10": reciprocal_rank(ranked, relevant, 10),
                "ndcg@10": ndcg_at_k(ranked, grades, 10),
                "recall@10": recall_at_k(ranked, relevant, 10),
            },
        })

    metric_names = ("mrr@10", "ndcg@10", "recall@10")
    return {
        "profile": profile,
        "query_count": len(per_query),
        "overall": {
            **{
                name: _mean(row["metrics"][name] for row in per_query)
                for name in metric_names
            },
            "forbidden_leaks": sum(len(row["forbidden"]) for row in per_query),
        },
        "per_query": per_query,
    }


def compare_runs(released: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    released_queries = {row["id"]: row for row in released["per_query"]}
    candidate_queries = {row["id"]: row for row in candidate["per_query"]}
    if released_queries.keys() != candidate_queries.keys():
        raise ValueError("released and candidate runs must contain the same queries")
    groups = {"wins": [], "losses": [], "unchanged": []}
    for query_id in sorted(released_queries):
        before = released_queries[query_id]["metrics"]["ndcg@10"]
        after = candidate_queries[query_id]["metrics"]["ndcg@10"]
        bucket = "wins" if after > before else "losses" if after < before else "unchanged"
        groups[bucket].append(query_id)
    metrics = ("mrr@10", "ndcg@10", "recall@10")
    return {
        **groups,
        "released": released["profile"],
        "candidate": candidate["profile"],
        "overall_delta": {
            metric: candidate["overall"][metric] - released["overall"][metric]
            for metric in metrics
        },
        "candidate_forbidden_leaks": candidate["overall"].get("forbidden_leaks", 0),
    }


def evaluate_grounding(
    answer: Mapping[str, Any], opened: set[tuple[str, str]]
) -> dict[str, Any]:
    citations = [
        (citation.get("source"), citation.get("chunk_id"))
        for citation in answer.get("citations", [])
        if isinstance(citation, Mapping)
    ]
    duplicates = len(citations) - len(set(citations))
    unopened = sorted(citation for citation in set(citations) if citation not in opened)
    complete_without_evidence = (
        answer.get("evidence_status") == "complete" and not citations
    )
    return {
        "grounded": not duplicates and not unopened and not complete_without_evidence,
        "duplicate_citations": duplicates,
        "unopened_citations": unopened,
    }
