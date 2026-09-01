import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import knowledge_browser.bulk_cli as bulk_cli
from knowledge_browser.bulk_verify import VerificationReport
from knowledge_browser.bulk_writer import BatchReport


pytestmark = pytest.mark.unit

REDWOOD_URL = "postgresql://postgres:postgres@127.0.0.1:5433/knowledge_redwood"
SCRIPT = Path(__file__).parents[2] / "scripts" / "redwood_database.sh"


def _safe_cli(monkeypatch, dataset=None):
    dataset = dataset or SimpleNamespace(root=Path("/safe/data"))
    monkeypatch.setattr(bulk_cli, "database_url", lambda: REDWOOD_URL)
    monkeypatch.setattr(
        bulk_cli, "validate_streaming_dataset", lambda _path: dataset
    )
    return dataset


def test_reset_validates_before_reset(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(bulk_cli, "database_url", lambda: REDWOOD_URL)
    monkeypatch.setattr(
        bulk_cli,
        "validate_streaming_dataset",
        lambda _path: (_ for _ in ()).throw(ValueError("bad manifest")),
    )
    monkeypatch.setattr(
        bulk_cli,
        "reset_redwood_database",
        lambda *_args: calls.append("reset"),
    )

    assert bulk_cli.main(["reset", "--data", str(tmp_path), "--yes"]) == 1
    assert calls == []


def test_reset_requires_explicit_yes_before_validation(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        bulk_cli,
        "validate_streaming_dataset",
        lambda _path: calls.append("validate"),
    )
    monkeypatch.setattr(
        bulk_cli,
        "reset_redwood_database",
        lambda *_args: calls.append("reset"),
    )

    assert bulk_cli.main(["reset", "--data", str(tmp_path)]) == 1
    assert calls == []


def test_cli_hides_exception_details(monkeypatch, capsys):
    _safe_cli(monkeypatch)
    monkeypatch.setattr(
        bulk_cli,
        "run_import",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("secret text")
        ),
    )

    assert bulk_cli.main(["run", "--data", "/safe/data"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Redwood import failed; run status for safe details.\n"


def test_run_does_not_require_key_when_importer_does_not_request_client(
    monkeypatch, capsys
):
    _safe_cli(monkeypatch)
    created = []
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        bulk_cli,
        "_openai_client",
        lambda: created.append("client") or object(),
    )
    monkeypatch.setattr(
        bulk_cli,
        "run_import",
        lambda _factory, _dataset, _client_factory, **_kwargs: SimpleNamespace(
            run_id="run-1", complete=True, batches=(), provider_calls=0
        ),
    )

    assert bulk_cli.main(["run", "--data", "/safe/data"]) == 0
    assert created == []
    assert "run=run-1 load_complete=yes provider_calls=0" in capsys.readouterr().out


def test_run_refuses_normal_database_before_import(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bulk_cli,
        "database_url",
        lambda: "postgresql://postgres:postgres@127.0.0.1/knowledge_search",
    )
    monkeypatch.setattr(
        bulk_cli,
        "validate_streaming_dataset",
        lambda _path: SimpleNamespace(root=Path("/safe/data")),
    )
    monkeypatch.setattr(
        bulk_cli,
        "run_import",
        lambda *_args, **_kwargs: calls.append("run"),
    )

    assert bulk_cli.main(["run", "--data", "/safe/data"]) == 1
    assert calls == []


def test_run_prints_safe_batch_progress(monkeypatch, tmp_path, capsys):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "slack.jsonl").write_bytes(b"one\ntwo\n")
    for source in ("jira", "github", "confluence"):
        (artifacts / f"{source}.jsonl").write_bytes(b"")
    _safe_cli(monkeypatch, SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(
        bulk_cli,
        "run_import",
        lambda *_args, **_kwargs: SimpleNamespace(
            run_id="run-1",
            complete=False,
            provider_calls=2,
            batches=(BatchReport(2, 3, 4, 3, 8, 2, "slack"),),
        ),
    )
    times = iter((10.0, 11.25))
    monkeypatch.setattr(bulk_cli.time, "monotonic", lambda: next(times))

    assert bulk_cli.main(["run", "--data", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert (
        "source=slack next_line=3 documents=2 sentences=4 "
        "elapsed_seconds=1.25 provider_calls=2"
    ) in output
    assert "one" not in output
    assert "two" not in output


def test_resumed_run_prints_the_batch_source_not_the_first_dataset_source(
    monkeypatch, tmp_path, capsys
):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for source in ("slack", "jira", "github", "confluence"):
        (artifacts / f"{source}.jsonl").write_bytes(b"record\n")
    _safe_cli(monkeypatch, SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(
        bulk_cli,
        "run_import",
        lambda *_args, **_kwargs: SimpleNamespace(
            run_id="run-1",
            complete=False,
            provider_calls=1,
            batches=(
                SimpleNamespace(
                    source="jira",
                    documents=1,
                    chunks=2,
                    sentences=3,
                    next_line=42,
                    next_offset=999,
                    provider_calls=1,
                ),
            ),
        ),
    )
    times = iter((10.0, 11.0))
    monkeypatch.setattr(bulk_cli.time, "monotonic", lambda: next(times))

    assert bulk_cli.main(["run", "--data", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "source=jira next_line=42" in output
    assert "source=slack" not in output


class _StatusRows:
    def __init__(self, *, one=None, rows=()):
        self.one = one
        self.rows = rows

    def fetchone(self):
        return self.one

    def __iter__(self):
        return iter(self.rows)


class _StatusConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement, _params=None):
        if "to_regclass" in statement:
            return _StatusRows(one=("bulk_import_runs",))
        if "FROM public.bulk_import_runs" in statement:
            return _StatusRows(
                one=(
                    "run-1",
                    "loading",
                    None,
                    "redwood-v1",
                    "abc123",
                    "text-embedding-3-small",
                    1536,
                    12.5,
                )
            )
        return _StatusRows(rows=(("jira", 42, 10, 20, 30),))


def test_status_prints_safe_manifest_identity_without_embedding_client(
    monkeypatch, capsys
):
    monkeypatch.setattr(bulk_cli, "database_url", lambda: REDWOOD_URL)
    monkeypatch.setattr(
        bulk_cli.psycopg,
        "connect",
        lambda _url: _StatusConnection(),
    )
    monkeypatch.setattr(
        bulk_cli,
        "_openai_client",
        lambda: (_ for _ in ()).throw(AssertionError("client created")),
    )

    assert bulk_cli.main(["status"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "run=run-1 status=loading dataset_version=redwood-v1 "
        "manifest_digest=abc123 embedding_model=text-embedding-3-small "
        "dimensions=1536 elapsed_seconds=12.50",
        "source=jira next_line=42 documents=10 chunks=20 sentences=30",
    ]


class _ContextConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_reset_defers_large_indexes_after_schema_reset(monkeypatch, tmp_path):
    calls = []
    _safe_cli(monkeypatch, SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(
        bulk_cli,
        "reset_redwood_database",
        lambda *_args: calls.append("reset"),
    )
    monkeypatch.setattr(
        bulk_cli,
        "_connection_factory",
        lambda _url: lambda: _ContextConnection(),
    )
    monkeypatch.setattr(
        bulk_cli,
        "prepare_bulk_load",
        lambda _conn: calls.append("defer_indexes"),
    )

    assert bulk_cli.main(["reset", "--data", str(tmp_path), "--yes"]) == 0
    assert calls == ["reset", "defer_indexes"]


def _verification_report(*, compatible=True):
    return VerificationReport(
        compatible=compatible,
        counts={"documents": 8, "chunks": 12, "sentences": 20},
        sources={"confluence": 1, "github": 1, "jira": 5, "slack": 1},
        missing_embeddings=0,
        acl_checks={"unknown_user_results": 0},
        recall_at_10=0.75,
        mrr=0.5,
        p50_ms=10.0,
        p95_ms=20.0,
    )


def test_verify_prints_safe_json_and_uses_released_profile(monkeypatch, capsys):
    dataset = _safe_cli(monkeypatch)
    calls = []
    monkeypatch.setattr(
        bulk_cli, "assert_redwood_database", lambda url: calls.append(("guard", url))
    )
    monkeypatch.setattr(
        bulk_cli,
        "verify_redwood",
        lambda factory, data, client, profile: calls.append(
            ("verify", data, profile.name)
        )
        or _verification_report(),
    )
    monkeypatch.setattr(bulk_cli, "_openai_client", lambda: object())

    assert bulk_cli.main(["verify", "--data", str(dataset.root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["compatible"] is True
    assert payload["counts"]["documents"] == 8
    assert calls == [
        ("guard", REDWOOD_URL),
        ("verify", dataset.root, "released"),
    ]


def test_verify_returns_failure_after_printing_incompatible_report(
    monkeypatch, capsys
):
    _safe_cli(monkeypatch)
    monkeypatch.setattr(bulk_cli, "assert_redwood_database", lambda _url: None)
    monkeypatch.setattr(
        bulk_cli, "verify_redwood", lambda *_args: _verification_report(compatible=False)
    )
    monkeypatch.setattr(bulk_cli, "_openai_client", lambda: object())

    assert bulk_cli.main(["verify", "--data", "/safe/data", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["compatible"] is False


def _executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o755)
    return path


def test_wrapper_refuses_unmanaged_container_name_conflict(tmp_path):
    log = tmp_path / "docker.log"
    docker = _executable(
        tmp_path / "docker",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >>\"$FAKE_LOG\"\n"
        "if [ \"$1\" = inspect ]; then exit 0; fi\n",
    )
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_LOG": str(log),
        "DOCKER_BIN": str(docker),
    }

    completed = subprocess.run(
        ["bash", str(SCRIPT), "start"],
        cwd=SCRIPT.parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == (
        "knowledge-redwood-db already exists outside this Compose project; "
        "remove it explicitly before start.\n"
    )
    assert "compose --profile redwood up" not in log.read_text()


@pytest.mark.parametrize("command", [("reset", "--yes"), ("run",)])
def test_wrapper_refuses_writes_to_an_unmanaged_container(tmp_path, command):
    marker = tmp_path / "python-ran"
    docker = _executable(
        tmp_path / "docker",
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = inspect ]; then exit 0; fi\n",
    )
    python = _executable(
        tmp_path / "python",
        "#!/usr/bin/env bash\n"
        f"touch {marker}\n"
        "printf '%s\\n' 'postgresql://postgres:postgres@127.0.0.1:5433/knowledge_redwood'\n",
    )
    env = {
        **os.environ,
        "DOCKER_BIN": str(docker),
        "PYTHON_BIN": str(python),
    }

    completed = subprocess.run(
        ["bash", str(SCRIPT), *command],
        cwd=SCRIPT.parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert completed.stderr == (
        "knowledge-redwood-db is not managed by this Compose project; "
        "run start or complete the explicit handoff first.\n"
    )
    assert not marker.exists()


def test_wrapper_start_finds_compose_file_outside_repo(tmp_path):
    docker = _executable(
        tmp_path / "docker",
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = inspect ]; then exit 1; fi\n"
        "case \" $* \" in\n"
        f"  *\" --project-directory {SCRIPT.parents[1]} \"*) exit 0 ;;\n"
        "  *) exit 7 ;;\n"
        "esac\n",
    )
    env = {**os.environ, "DOCKER_BIN": str(docker)}

    completed = subprocess.run(
        ["bash", str(SCRIPT), "start"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0


def test_wrapper_does_not_run_python_from_the_current_directory(tmp_path):
    marker = tmp_path / "unexpected-python"
    _executable(tmp_path / "git", "#!/usr/bin/env bash\nexit 1\n")
    python = tmp_path / "api" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    _executable(
        python,
        "#!/usr/bin/env bash\n"
        f"touch {marker}\n"
        "printf '%s\\n' 'postgresql://postgres:postgres@127.0.0.1:5433/knowledge_redwood'\n",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHON_BIN", "PYTHONPATH"}
    }
    env["PATH"] = f"{tmp_path}:{env['PATH']}"

    completed = subprocess.run(
        ["bash", str(SCRIPT), "validate"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert completed.stderr == (
        "Redwood Python environment is missing; install the API dependencies.\n"
    )
    assert not marker.exists()


def test_wrapper_overrides_normal_database_url(tmp_path):
    log = tmp_path / "python.log"
    python = _executable(
        tmp_path / "python",
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = -c ]; then\n"
        "  printf '%s\\n' "
        "'postgresql://redwood-user:redwood-password@127.0.0.1:5544/knowledge_redwood'\n"
        "  exit 0\n"
        "fi\n"
        f"case :$PYTHONPATH: in *:{SCRIPT.parents[1]}/api/src:*) ;; *) exit 8 ;; esac\n"
        "printf 'DATABASE_URL=%s\\n' \"$DATABASE_URL\" >\"$FAKE_LOG\"\n"
        "printf 'ARGS=%s\\n' \"$*\" >>\"$FAKE_LOG\"\n",
    )
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql://normal:normal@normal.invalid/knowledge_search",
        "REDWOOD_POSTGRES_USER": "redwood-user",
        "REDWOOD_POSTGRES_PASSWORD": "redwood-password",
        "REDWOOD_POSTGRES_PORT": "5544",
        "FAKE_LOG": str(log),
        "PYTHON_BIN": str(python),
    }

    completed = subprocess.run(
        ["bash", str(SCRIPT), "validate", "--data", "/safe/data"],
        cwd=SCRIPT.parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert log.read_text().splitlines() == [
        "DATABASE_URL=postgresql://redwood-user:redwood-password@"
        "127.0.0.1:5544/knowledge_redwood",
        "ARGS=-m knowledge_browser.bulk_cli validate --data /safe/data",
    ]
