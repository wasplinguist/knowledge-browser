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
NOW = datetime(2026, 9, 1, 1, tzinfo=timezone.utc)


def _manifest(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "evidence").mkdir()
    (root / "search" / "profiles" / "candidates").mkdir(parents=True)
    (root / "eval").mkdir()
    (root / "evidence" / "weekly.json").write_text(json.dumps({
        "since": "2026-08-25T00:00:00+00:00",
        "until": "2026-09-01T00:00:00+00:00",
        "total_searches": 3,
        "unique_queries": 2,
        "no_result_rate": 0.5,
        "click_through_rate": 0.5,
        "p50_duration_ms": 20,
        "p95_duration_ms": 40,
        "top_queries": [{"query": "NREL", "searches": 2}],
        "top_no_result_queries": [{"query": "NREL", "searches": 2}],
        "top_unclicked_queries": [{"query": "NREL", "searches": 2}],
        "reformulations": [],
        "excluded_profiles": ["demo-loop-v1"],
    }))
    (root / "search" / "profiles" / "released.json").write_text(
        '{"name":"released","query_expansions":{}}'
    )
    (root / "search" / "profiles" / "candidates" / "candidate.json").write_text(
        '{"name":"candidate","query_expansions":{"NREL":"Nimbus Relay"}}'
    )
    (root / "eval" / "redwood_queries.json").write_text(json.dumps([{
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
        "baseline_profile": "search/profiles/released.json",
        "challenger_profile": "search/profiles/candidates/candidate.json",
        "golden_queries": "eval/redwood_queries.json",
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
    manifest = validate_manifest(path, tmp_path, now=lambda: NOW)
    assert manifest["id"] == "exp-nrel"

    data = json.loads(path.read_text())
    data["intent_audit"]["verdict"] = "UNCLEAR"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="ALIGNED"):
        validate_manifest(path, tmp_path, now=lambda: NOW)

    data["intent_audit"]["verdict"] = "ALIGNED"
    data["challenger_profile"] = data["baseline_profile"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="differ"):
        validate_manifest(path, tmp_path, now=lambda: NOW)


def test_manifest_rejects_stale_empty_or_malformed_behavior_evidence(tmp_path):
    path = _manifest(tmp_path)
    evidence_path = tmp_path / "evidence" / "weekly.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["until"] = "2026-08-26T00:00:00+00:00"
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="fresh"):
        validate_manifest(path, tmp_path, now=lambda: NOW)

    evidence["until"] = "2026-09-01T00:00:00+00:00"
    evidence["total_searches"] = 0
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="useful"):
        validate_manifest(path, tmp_path, now=lambda: NOW)

    evidence["total_searches"] = 3
    evidence["excluded_profiles"] = []
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="excluded"):
        validate_manifest(path, tmp_path, now=lambda: NOW)


def test_manifest_requires_failure_evidence_and_a_complete_hypothesis_chain(tmp_path):
    path = _manifest(tmp_path)
    evidence_path = tmp_path / "evidence" / "weekly.json"
    evidence = json.loads(evidence_path.read_text())
    for field in ("top_no_result_queries", "top_unclicked_queries", "reformulations"):
        evidence[field] = []
    evidence["no_result_rate"] = 0
    evidence["click_through_rate"] = 1
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="failure or reformulation"):
        validate_manifest(path, tmp_path, now=lambda: NOW)

    path = _manifest(tmp_path / "empty-insight")
    manifest = json.loads(path.read_text())
    manifest["hypothesis"] = "  "
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="hypothesis"):
        validate_manifest(path, tmp_path / "empty-insight", now=lambda: NOW)

    path = _manifest(tmp_path / "bad-golden-change")
    manifest = json.loads(path.read_text())
    manifest["golden_changes"] = [{"query_id": "q1", "change": "new label"}]
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="golden change"):
        validate_manifest(path, tmp_path / "bad-golden-change", now=lambda: NOW)


def test_manifest_rejects_malformed_weekly_report_rows_and_numbers(tmp_path):
    path = _manifest(tmp_path)
    evidence_path = tmp_path / "evidence" / "weekly.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["top_queries"] = [None]
    evidence["top_no_result_queries"] = [None]
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="fields are invalid"):
        validate_manifest(path, tmp_path, now=lambda: NOW)

    path = _manifest(tmp_path / "bad-numbers")
    evidence_path = tmp_path / "bad-numbers" / "evidence" / "weekly.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["no_result_rate"] = True
    evidence["click_through_rate"] = 1.5
    evidence["p50_duration_ms"] = 50
    evidence["p95_duration_ms"] = 20
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="fields are invalid"):
        validate_manifest(path, tmp_path / "bad-numbers", now=lambda: NOW)

    path = _manifest(tmp_path / "bad-reformulation")
    evidence_path = tmp_path / "bad-reformulation" / "evidence" / "weekly.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["top_no_result_queries"] = []
    evidence["top_unclicked_queries"] = []
    evidence["reformulations"] = [{"session_id": "", "queries": ["one"]}]
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="fields are invalid"):
        validate_manifest(path, tmp_path / "bad-reformulation", now=lambda: NOW)


def test_manifest_requires_released_baseline_and_real_behavior_change(tmp_path):
    path = _manifest(tmp_path)
    data = json.loads(path.read_text())
    data["baseline_profile"] = data["challenger_profile"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="released profile"):
        validate_manifest(path, tmp_path, now=lambda: NOW)

    path = _manifest(tmp_path / "second")
    candidate = tmp_path / "second" / "search" / "profiles" / "candidates" / "candidate.json"
    candidate.write_text('{"name":"renamed","query_expansions":{}}')
    with pytest.raises(ValueError, match="behavior settings"):
        validate_manifest(path, tmp_path / "second", now=lambda: NOW)


def test_manifest_rejects_bad_or_ambiguous_query_embeddings(tmp_path):
    path = _manifest(tmp_path)
    embeddings_path = tmp_path / "eval" / "embeddings.json"
    embeddings_path.write_text(json.dumps({"q1": [0.0]}))
    with pytest.raises(ValueError, match="1,536"):
        validate_manifest(path, tmp_path, now=lambda: NOW)

    data = json.loads((tmp_path / "eval" / "redwood_queries.json").read_text())
    data.append({**data[0], "id": "q2"})
    (tmp_path / "eval" / "redwood_queries.json").write_text(json.dumps(data))
    embeddings_path.write_text(json.dumps({
        "q1": [0.0] * 1536,
        "q2": [1.0] * 1536,
    }))
    with pytest.raises(ValueError, match="same text"):
        validate_manifest(path, tmp_path, now=lambda: NOW)


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
    assert "u18" in selected_users


def test_decision_recommends_only_the_separate_release_gate():
    assert decide(_evaluation()) == "recommend-release-gate"
    worse = _evaluation()
    worse["fast_acl"]["child_leaks"] = [{"document": "secret"}]
    assert decide(worse) == "reject"
    slow = _evaluation()
    slow["latency_ms"] = {"baseline": 100, "candidate": 500}
    assert decide(slow) == "reject"


def test_run_requires_new_output_and_writes_hashed_json_and_easy_html(tmp_path):
    path = _manifest(tmp_path)
    output = tmp_path / "artifacts" / "fresh-run"

    report = run_experiment(
        path,
        output,
        root=tmp_path,
        evaluate=lambda _manifest, _paths: _evaluation(),
        now=lambda: NOW,
        git_sha="abc123",
        command=["scripts/run_eval_loop.py", "evaluate"],
    )

    payload = json.loads((output / "run.json").read_text())
    html = report.read_text()
    assert payload["decision"] == "recommend-release-gate"
    assert payload["provenance"]["git_sha"] == "abc123"
    assert payload["provenance"]["source_sha256"] == "unknown"
    assert payload["provenance"]["command"] == [
        "scripts/run_eval_loop.py", "evaluate"
    ]
    assert set(payload["provenance"]["sha256"]) == {
        "manifest", "evidence_report", "baseline_profile", "challenger_profile",
        "golden_queries", "query_embeddings",
    }
    assert "People rewrite NREL" in html
    assert "0.500" in html and "0.530" in html
    assert "13" in html
    assert "q1" in html
    assert payload["provenance"]["sha256"]["evidence_report"] in html
    assert "No profile was promoted" in html

    empty = tmp_path / "already-exists"
    empty.mkdir()
    with pytest.raises(ValueError, match="must not already exist"):
        run_experiment(
            path, empty, root=tmp_path,
            evaluate=lambda _manifest, _paths: _evaluation(),
            now=lambda: NOW,
        )

    with pytest.raises(ValueError, match="must not already exist"):
        run_experiment(
            path, output, root=tmp_path,
            evaluate=lambda _manifest, _paths: _evaluation(),
            now=lambda: NOW,
        )


def test_run_rejects_inputs_changed_during_evaluation(tmp_path):
    path = _manifest(tmp_path)

    def change_input(_manifest, paths):
        paths["evidence_report"].write_text('{"changed": true}')
        return _evaluation()

    with pytest.raises(ValueError, match="changed during evaluation"):
        run_experiment(
            path, tmp_path / "output", root=tmp_path,
            evaluate=change_input, now=lambda: NOW,
        )


def test_run_rejects_source_changed_during_evaluation(tmp_path):
    path = _manifest(tmp_path)
    states = iter(["clean-source", "changed-source"])
    with pytest.raises(ValueError, match="source changed"):
        run_experiment(
            path, tmp_path / "output", root=tmp_path,
            evaluate=lambda _manifest, _paths: _evaluation(),
            now=lambda: NOW,
            source_state=lambda: next(states),
        )
