import importlib.util
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

from conftest import _prepare_test_database, _seed
from knowledge_browser.db_compat import check_compatibility
import knowledge_browser.bootstrap as bootstrap

pytestmark = pytest.mark.integration

DATA = Path(__file__).parents[2] / "data" / "company"
VECTOR = [0.0] * 1536


class FakeEmbeddingClient:
    def __init__(self):
        self.calls = 0
        self.embeddings = self

    def create(self, *, model, input):
        self.calls += 1
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=VECTOR)
                for index, _ in enumerate(input)
            ]
        )


@pytest.fixture
def connection_factory():
    database_url = _prepare_test_database()
    return lambda: psycopg.connect(database_url)


def test_bootstrap_module_exports_the_public_interface():
    spec = importlib.util.find_spec("knowledge_browser.bootstrap")

    assert spec is not None


def test_populated_database_skips_provider(connection_factory):
    with connection_factory() as conn:
        _seed(conn)

    called = False

    def client_factory():
        nonlocal called
        called = True
        raise AssertionError("provider must not run")

    result = bootstrap.bootstrap_database(connection_factory, DATA, client_factory)

    assert result.imported is False
    assert result.report is None
    assert called is False


def test_partial_database_is_refused(connection_factory):
    with connection_factory() as conn:
        conn.execute(
            "INSERT INTO users (email, name) VALUES (%s, %s)",
            ("partial@example.test", "Partial User"),
        )

    with pytest.raises(bootstrap.BootstrapError, match="partially initialized"):
        bootstrap.bootstrap_database(
            connection_factory,
            DATA,
            lambda: (_ for _ in ()).throw(AssertionError("provider must not run")),
        )


def test_incompatible_populated_database_is_refused(connection_factory):
    with connection_factory() as conn:
        _seed(conn)
        conn.execute("DROP INDEX sentences_embedding_idx")

    with pytest.raises(bootstrap.BootstrapError, match="existing database is incompatible"):
        bootstrap.bootstrap_database(
            connection_factory,
            DATA,
            lambda: (_ for _ in ()).throw(AssertionError("provider must not run")),
        )


def test_empty_database_imports_with_fake_embeddings(connection_factory):
    client = FakeEmbeddingClient()

    result = bootstrap.bootstrap_database(connection_factory, DATA, lambda: client)

    assert result.imported is True
    assert (result.report.users, result.report.documents, result.report.chunks, result.report.sentences) == (
        100,
        1000,
        13145,
        16520,
    )
    assert client.calls > 0
    with connection_factory() as conn:
        assert check_compatibility(conn).compatible is True


def test_provider_failure_leaves_database_empty(connection_factory):
    with pytest.raises(RuntimeError, match="provider unavailable"):
        bootstrap.bootstrap_database(
            connection_factory,
            DATA,
            lambda: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
        )

    with connection_factory() as conn:
        assert conn.execute("SELECT count(*) FROM documents").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM users").fetchone() == (0,)


def test_post_import_compatibility_failure_rolls_back_outer_transaction(
    connection_factory, monkeypatch
):
    monkeypatch.setattr(
        bootstrap,
        "load_dataset",
        lambda _data_dir: SimpleNamespace(documents=()),
    )
    monkeypatch.setattr(bootstrap, "collect_sentences", lambda _documents: [])
    monkeypatch.setattr(bootstrap, "create_embeddings", lambda *_args: {})

    def import_one_user(conn, *_args, **_kwargs):
        conn.execute(
            "INSERT INTO users (email, name) VALUES (%s, %s)",
            ("rolled-back@example.test", "Rolled Back"),
        )
        return SimpleNamespace(users=1, documents=0, chunks=0, sentences=0)

    monkeypatch.setattr(bootstrap, "import_dataset", import_one_user)
    monkeypatch.setattr(
        bootstrap,
        "check_compatibility",
        lambda _conn: SimpleNamespace(compatible=False),
    )

    with pytest.raises(bootstrap.BootstrapError, match="imported database failed compatibility check"):
        bootstrap.bootstrap_database(connection_factory, DATA, FakeEmbeddingClient)

    with connection_factory() as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone() == (0,)


def test_cli_skips_populated_database_without_an_api_key(
    connection_factory, monkeypatch, capsys
):
    with connection_factory() as conn:
        _seed(conn)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(bootstrap, "connection", connection_factory)

    assert bootstrap.main(["--data", str(DATA)]) == 0

    output = capsys.readouterr()
    assert output.out == "database already initialized\n"
    assert output.err == ""


def test_cli_never_prints_exception_details(monkeypatch, capsys):
    monkeypatch.setattr(
        bootstrap,
        "bootstrap_database",
        lambda *_args: (_ for _ in ()).throw(
            RuntimeError("https://provider.invalid payload secret-key")
        ),
    )

    assert bootstrap.main(["--data", str(DATA)]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "bootstrap failed\n"
