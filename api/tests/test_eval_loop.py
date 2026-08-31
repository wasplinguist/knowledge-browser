from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import UUID

import pytest

from knowledge_browser.eval_loop import (
    decide,
    run_experiment,
    select_fast_acl_inputs,
    validate_manifest,
)


pytestmark = pytest.mark.unit


def _manifest(root: Path) -> Path:
    (root / "evidence").mkdir()
    (root / "profiles").mkdir()
    (root / "eval").mkdir()
    (root / "evidence" / "weekly.json").write_text('{"total_searches": 3}')
    (root / "profiles" / "released.json").write_text(
        '{"name":"released","query_expansions":{}}'
    )
    (root / "profiles" / "candidate.json").write_text(
        '{"name":"candidate","query_expansions":{"NREL":"Nimbus Relay"}}'
    )
    (root / "eval" / "queries.json").write_text(json.dumps([{
        "id": "q1", "as_user": "u1", "query": "NREL status",
        "type": "acronym_alias", "acl_aware": True,
        "relevant": ["jira:COMPANY-1"],
    }]))
    (root / "eval" / "embeddings.json").write_text(
        json.dumps({"q1": [0.0] * 1536})
    )
    path = root / "experiment.json"
    path.write_text(json.dumps({
        "id": "exp-nrel",
        "created_at": "2026-09-01T00:00:00Z",
        "evidence_report": "evidence/weekly.json",
        "insight": "People rewrite NREL as Nimbus Relay.",
        "hypothesis": "Expansion improves alias nDCG@10.",
        "implementation": "Add one whole-term query expansion.",
        "affected_intents": ["acronym_alias"],
        "target_metrics": ["ndcg@10"],
        "regression_risk": "Known Jira keys may move.",
        "intent_audit": {"verdict": "ALIGNED", "evidence": "weekly"},
        "baseline_profile": "profiles/released.json",
        "challenger_profile": "profiles/candidate.json",
        "golden_queries": "eval/queries.json",
        "query_embeddings": "eval/embeddings.json",
        "golden_changes": [],
        "golden_change_reason": "Existing query measures the failure.",
        "status": "implemented",
    }))
    return path


def _evaluation():
    return {
        "baseline": {"overall": {"ndcg@10": 0.50, "recall@10": 0.70}},
        "candidate": {
            "overall": {
                "ndcg@10": 0.53, "recall@10": 0.70,
                "forbidden_leaks": 0,
            }
        },
        "comparison": {
            "wins": ["q1"], "losses": [], "unchanged": [],
            "overall_delta": {"ndcg@10": 0.03, "recall@10": 0.0},
        },
        "fast_acl": {"pairs": 13, "root_leaks": [], "child_leaks": []},
        "latency_ms": {"baseline": 20, "candidate": 21},
    }


def test_manifest_requires_fresh_evidence_aligned_audit_and_distinct_profiles(tmp_path):
    path = _manifest(tmp_path)
    manifest = validate_manifest(path, tmp_path)
    assert manifest["id"] == "exp-nrel"

    data = json.loads(path.read_text())
    data["intent_audit"]["verdict"] = "UNCLEAR"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="ALIGNED"):
        validate_manifest(path, tmp_path)

    data["intent_audit"]["verdict"] = "ALIGNED"
    data["challenger_profile"] = data["baseline_profile"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="differ"):
        validate_manifest(path, tmp_path)


def test_manifest_rejects_bad_or_ambiguous_query_embeddings(tmp_path):
    path = _manifest(tmp_path)
    embeddings_path = tmp_path / "eval" / "embeddings.json"
    embeddings_path.write_text(json.dumps({"q1": [0.0]}))
    with pytest.raises(ValueError, match="1,536"):
        validate_manifest(path, tmp_path)

    data = json.loads((tmp_path / "eval" / "queries.json").read_text())
    data.append({**data[0], "id": "q2"})
    (tmp_path / "eval" / "queries.json").write_text(json.dumps(data))
    embeddings_path.write_text(json.dumps({
        "q1": [0.0] * 1536,
        "q2": [1.0] * 1536,
    }))
    with pytest.raises(ValueError, match="same text"):
        validate_manifest(path, tmp_path)


def test_fast_acl_sample_is_deterministic_and_includes_acl_queries_and_owners():
    queries = [
        {"id": "acl", "type": "known_item", "as_user": "u3", "acl_aware": True},
        {"id": "a", "type": "alias", "as_user": "u2"},
        {"id": "b", "type": "alias", "as_user": "u1"},
        {"id": "c", "type": "alias", "as_user": "u4"},
    ]
    users = ["u4", "u3", "u2", "u1", *[f"u{x:02}" for x in range(20)]]

    selected_queries, selected_users = select_fast_acl_inputs(queries, users)

    assert [item["id"] for item in selected_queries] == ["acl", "a", "b"]
    assert {"u1", "u2", "u3"} <= set(selected_users)
    assert selected_users == select_fast_acl_inputs(queries, users)[1]
    assert len(selected_queries) * len(selected_users) < len(queries) * len(users)


def test_decision_recommends_only_the_separate_release_gate():
    assert decide(_evaluation()) == "recommend-release-gate"
    worse = _evaluation()
    worse["fast_acl"]["child_leaks"] = [{"document": "secret"}]
    assert decide(worse) == "reject"


def test_run_requires_new_output_and_writes_hashed_json_and_easy_html(tmp_path):
    path = _manifest(tmp_path)
    output = tmp_path / "artifacts" / "fresh-run"

    report = run_experiment(
        path,
        output,
        root=tmp_path,
        evaluate=lambda _manifest, _paths: _evaluation(),
        now=lambda: datetime(2026, 9, 1, 1, tzinfo=timezone.utc),
        git_sha="abc123",
        command=["scripts/run_eval_loop.py", "evaluate"],
    )

    payload = json.loads((output / "run.json").read_text())
    html = report.read_text()
    assert payload["decision"] == "recommend-release-gate"
    assert payload["provenance"]["git_sha"] == "abc123"
    assert payload["provenance"]["command"] == [
        "scripts/run_eval_loop.py", "evaluate"
    ]
    assert set(payload["provenance"]["sha256"]) == {
        "evidence", "baseline_profile", "challenger_profile", "golden_queries",
        "query_embeddings",
    }
    assert "People rewrite NREL" in html
    assert "0.500" in html and "0.530" in html
    assert "13" in html
    assert "q1" in html
    assert payload["provenance"]["sha256"]["evidence"] in html
    assert "No profile was promoted" in html

    with pytest.raises(ValueError, match="new and empty"):
        run_experiment(
            path, output, root=tmp_path,
            evaluate=lambda _manifest, _paths: _evaluation(),
        )
