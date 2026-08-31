import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import test_database_url
from knowledge_browser.db_compat import check_compatibility


pytestmark = pytest.mark.integration


def test_compatibility_reports_only_safe_aggregate_counts(db):
    report = check_compatibility(db)

    assert report.compatible is True
    assert report.issues == ()
    assert report.document_count == 8
    assert report.chunk_count == 8
    assert report.sentence_count == 8
    assert report.embedded_sentence_count == 8
    assert report.document_source_counts == {
        "confluence": 1,
        "github": 1,
        "jira": 5,
        "slack": 1,
    }
    serialized = json.dumps(report.safe_dict())
    assert "Company body" not in serialized
    assert "permission_set" not in serialized


def test_compatibility_fails_for_a_missing_required_index(db):
    db.execute("DROP INDEX sentences_embedding_idx")

    report = check_compatibility(db)

    assert report.compatible is False
    assert report.issues == ("missing index: sentences_embedding_idx",)


def test_compatibility_fails_for_a_missing_embedding(db):
    db.execute(
        "UPDATE sentences SET embedding = NULL "
        "WHERE source = 'jira' AND chunk_id = 'jira:COMPANY-1:0'"
    )

    report = check_compatibility(db)

    assert report.compatible is False
    assert "sentences contain missing embeddings" in report.issues


def test_cli_output_is_aggregate_only_and_never_echoes_credentials(prepared_test_database):
    secret = "do-not-print-this-password"
    env = {
        **os.environ,
        "DATABASE_URL": test_database_url().replace("postgres:postgres", f"postgres:{secret}"),
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }

    completed = subprocess.run(
        [sys.executable, "-m", "knowledge_browser.db_compat"],
        cwd=Path(__file__).parents[1],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "compatibility check failed: database connection unavailable\n"
    assert secret not in completed.stdout + completed.stderr


def test_test_database_guard_rejects_any_database_without_test(monkeypatch):
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/knowledge_search",
    )

    with pytest.raises(RuntimeError, match="dedicated _test database"):
        test_database_url()
