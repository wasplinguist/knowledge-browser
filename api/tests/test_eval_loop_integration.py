import json
from pathlib import Path

import pytest

from knowledge_browser.eval_loop import execute_evaluation, experiment_paths


pytestmark = pytest.mark.search_eval


def test_real_loop_evaluator_uses_same_queries_and_a_fast_acl_sample(db, tmp_path):
    root = tmp_path
    (root / "evidence").mkdir()
    (root / "profiles").mkdir()
    (root / "eval").mkdir()
    (root / "evidence" / "weekly.json").write_text('{"total_searches": 2}')
    released = json.loads(
        (Path(__file__).parents[2] / "search" / "profiles" / "released.json").read_text()
    )
    released["semantic_weight"] = 0
    candidate = {**released, "name": "candidate", "query_expansions": {"NREL": "Nimbus Relay"}}
    (root / "profiles" / "released.json").write_text(json.dumps(released))
    (root / "profiles" / "candidate.json").write_text(json.dumps(candidate))
    queries = json.loads(
        (Path(__file__).parents[2] / "eval" / "fixture_queries.json").read_text()
    )
    for index, query in enumerate(queries):
        query["type"] = "known_item" if index < 2 else "acl"
        query["acl_aware"] = index == 3
    (root / "eval" / "redwood_queries.json").write_text(json.dumps(queries))
    (root / "eval" / "embeddings.json").write_text(json.dumps({
        query["id"]: [0.0] * 1536 for query in queries
    }))
    manifest = {
        "id": "exp-smoke",
        "evidence_report": "evidence/weekly.json",
        "baseline_profile": "profiles/released.json",
        "challenger_profile": "profiles/candidate.json",
        "golden_queries": "eval/redwood_queries.json",
        "query_embeddings": "eval/embeddings.json",
    }

    evaluation = execute_evaluation(db, manifest, experiment_paths(manifest, root))

    assert evaluation["baseline"]["query_count"] == 4
    assert evaluation["candidate"]["query_count"] == 4
    assert evaluation["candidate"]["overall"]["forbidden_leaks"] == 0
    assert 0 < evaluation["fast_acl"]["pairs"] <= 4 * 4
    assert evaluation["fast_acl"]["root_leaks"] == []
    assert evaluation["fast_acl"]["child_leaks"] == []
    assert evaluation["latency_ms"]["baseline"] > 0
    assert evaluation["latency_ms"]["candidate"] > 0
