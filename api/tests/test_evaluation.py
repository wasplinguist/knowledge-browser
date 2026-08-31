import json

import pytest

from knowledge_browser.evaluation import (
    compare_runs,
    evaluate_queries,
    load_golden_queries,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    write_report,
)


pytestmark = pytest.mark.search_eval


def test_ranking_metrics_measure_recall_first_hit_and_order():
    ranked = ["b", "a", "x"]

    assert recall_at_k(ranked, {"a", "b"}, 2) == 1.0
    assert reciprocal_rank(ranked, {"a"}, 10) == 0.5
    assert ndcg_at_k(ranked, {"a": 2, "b": 1}, 2) < 1.0


def test_golden_loader_requires_unique_stable_queries(tmp_path):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps([
        {
            "id": "known-company",
            "as_user": "user-1",
            "query": "company",
            "relevant": ["COMPANY-1"],
            "grades": {"COMPANY-1": 2},
            "must_not_appear": ["HIDDEN-1"],
        }
    ]))

    assert load_golden_queries(path)[0]["id"] == "known-company"

    path.write_text(json.dumps([
        {"id": "same", "as_user": "u", "query": "a", "relevant": ["a"]},
        {"id": "same", "as_user": "u", "query": "b", "relevant": ["b"]},
    ]))
    with pytest.raises(ValueError, match="unique"):
        load_golden_queries(path)


def test_query_evaluation_reports_metrics_and_forbidden_leaks():
    queries = [{
        "id": "q1",
        "as_user": "u1",
        "query": "router",
        "relevant": ["jira:A", "jira:B"],
        "grades": {"jira:A": 2, "jira:B": 1},
        "must_not_appear": ["slack:SECRET"],
    }]

    run = evaluate_queries(
        queries,
        lambda _user, _query, _profile: [
            {"source": "jira", "external_id": "A"},
            {"source": "slack", "external_id": "SECRET"},
        ],
        profile="released",
    )

    assert run["profile"] == "released"
    assert run["query_count"] == 1
    assert run["overall"]["mrr@10"] == 1.0
    assert run["overall"]["recall@10"] == 0.5
    assert run["overall"]["forbidden_leaks"] == 1
    assert run["per_query"][0]["forbidden"] == ["slack:SECRET"]


def test_query_evaluation_keeps_same_external_id_from_two_sources_distinct():
    query = {
        "id": "q1",
        "as_user": "u1",
        "query": "shared",
        "relevant": ["jira:SAME"],
        "must_not_appear": ["slack:SAME"],
    }

    run = evaluate_queries(
        [query],
        lambda *_args: [
            {"source": "jira", "external_id": "SAME"},
            {"source": "slack", "external_id": "SAME"},
        ],
        profile="released",
    )

    assert run["per_query"][0]["ranked"] == ["jira:SAME", "slack:SAME"]
    assert run["per_query"][0]["forbidden"] == ["slack:SAME"]


def test_released_candidate_comparison_reports_wins_and_losses():
    released = {
        "profile": "released",
        "overall": {"mrr@10": 0.5, "ndcg@10": 0.4, "recall@10": 0.5},
        "per_query": [
            {"id": "better", "metrics": {"ndcg@10": 0.2}},
            {"id": "worse", "metrics": {"ndcg@10": 0.8}},
        ],
    }
    candidate = {
        "profile": "candidate",
        "overall": {"mrr@10": 0.6, "ndcg@10": 0.5, "recall@10": 0.5},
        "per_query": [
            {"id": "better", "metrics": {"ndcg@10": 0.7}},
            {"id": "worse", "metrics": {"ndcg@10": 0.6}},
        ],
    }

    comparison = compare_runs(released, candidate)

    assert comparison["wins"] == ["better"]
    assert comparison["losses"] == ["worse"]
    assert comparison["unchanged"] == []
    assert comparison["overall_delta"]["ndcg@10"] == pytest.approx(0.1)


def test_evaluation_report_is_written_as_stable_json(tmp_path):
    path = tmp_path / "nested" / "evaluation.json"

    write_report(path, {"wins": ["q1"], "mrr@10": 0.5})

    assert json.loads(path.read_text()) == {"wins": ["q1"], "mrr@10": 0.5}
