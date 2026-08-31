from contextlib import contextmanager
import json

import pytest

import conftest
from knowledge_browser import db_compat
from knowledge_browser.db_compat import CompatibilityReport


pytestmark = pytest.mark.unit


def test_unsafe_test_database_name_opens_zero_connections(monkeypatch):
    connection_attempts = 0

    def forbidden_connect(*_args, **_kwargs):
        nonlocal connection_attempts
        connection_attempts += 1
        raise AssertionError("unsafe database guard ran after connecting")

    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/knowledge_search",
    )
    monkeypatch.setattr(conftest.psycopg, "connect", forbidden_connect)

    with pytest.raises(RuntimeError, match="dedicated _test database"):
        conftest._prepare_test_database()

    assert connection_attempts == 0


def test_successful_cli_sets_read_only_before_check_and_prints_safe_aggregates(
    monkeypatch, capsys
):
    events = []
    secret = "cli-secret-must-not-print"
    monkeypatch.setenv(
        "DATABASE_URL", f"postgresql://postgres:{secret}@localhost/example"
    )

    class FakeConnection:
        def execute(self, query):
            events.append(query)

    @contextmanager
    def fake_connection():
        yield FakeConnection()

    def fake_check(conn):
        events.append(("check", conn))
        return CompatibilityReport(True, (), 4, 3, 2, 2, {"jira": 4})

    monkeypatch.setattr(db_compat, "connection", fake_connection)
    monkeypatch.setattr(db_compat, "check_compatibility", fake_check)

    assert db_compat.main() == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert events[0] == "SET TRANSACTION READ ONLY"
    assert events[1][0] == "check"
    assert payload == {
        "compatible": True,
        "counts": {
            "chunks": 3,
            "documents": 4,
            "documents_by_source": {"jira": 4},
            "embedded_sentences": 2,
            "sentences": 2,
        },
        "issues": [],
    }
    assert output.err == ""
    assert secret not in output.out + output.err
