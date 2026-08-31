import pytest


pytestmark = pytest.mark.unit


def test_database_url_prefers_database_url(monkeypatch):
    from knowledge_browser.config import database_url

    monkeypatch.setenv("DATABASE_URL", "postgresql://preferred.invalid/db")
    monkeypatch.setenv("POSTGRES_DB", "ignored")

    assert database_url() == "postgresql://preferred.invalid/db"


def test_database_url_uses_all_postgres_components_and_escapes_credentials(monkeypatch):
    from knowledge_browser.config import database_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "database.internal")
    monkeypatch.setenv("POSTGRES_PORT", "5544")
    monkeypatch.setenv("POSTGRES_DB", "knowledge_search")
    monkeypatch.setenv("POSTGRES_USER", "browser@example.test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret/with spaces")

    assert database_url() == (
        "postgresql://browser%40example.test:secret%2Fwith%20spaces@"
        "database.internal:5544/knowledge_search"
    )


@pytest.mark.parametrize(
    ("user_parameter", "document_alias"),
    [
        ("user-id", "documents"),
        ("user_id)s OR true --", "documents"),
        ("user_id", "documents.root"),
        ("user_id", "documents; DROP TABLE users"),
    ],
)
def test_acl_sql_rejects_malformed_identifiers(user_parameter, document_alias):
    from knowledge_browser.db import allowed_document_sql

    with pytest.raises(ValueError, match="SQL identifier"):
        allowed_document_sql(user_parameter, document_alias)


def test_acl_sql_accepts_only_the_requested_parameter_and_alias():
    from knowledge_browser.db import allowed_document_sql

    predicate = allowed_document_sql("viewer_id", "candidate")

    assert "%(viewer_id)s" in predicate
    assert "candidate.permission_set_id" in predicate
    assert "documents.permission_set_id" not in predicate
