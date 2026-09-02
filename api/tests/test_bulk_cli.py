import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import knowledge_browser.bulk_cli as bulk_cli
from knowledge_browser.bulk_state import BulkStateError
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
    assert captured.err == (
        "Redwood command failed: reason=import_failed; "
        "next_step=run status, fix the reported issue, then rerun run; "
        "do not reset valid progress.\n"
    )
    assert "secret text" not in captured.err


def test_invalid_manifest_has_safe_reason_and_next_step(monkeypatch, capsys):
    monkeypatch.setattr(
        bulk_cli,
        "validate_streaming_dataset",
        lambda _path: (_ for _ in ()).throw(
            ValueError("secret artifact content")
        ),
    )

    assert bulk_cli.main(["validate", "--data", "/safe/data"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Redwood command failed: reason=invalid_manifest; "
        "next_step=fix the dataset, then run validate again.\n"
    )
    assert "secret artifact content" not in captured.err


def test_changed_import_state_has_safe_reason_and_next_step(monkeypatch, capsys):
    _safe_cli(monkeypatch)
    error = BulkStateError("secret changed manifest detail")
    error.safe_code = "changed_state"
    monkeypatch.setattr(
        bulk_cli,
        "run_import",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    assert bulk_cli.main(["run", "--data", "/safe/data"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Redwood command failed: reason=changed_state; "
        "next_step=use the original dataset and model, or check the target "
        "before an intentional reset.\n"
    )
    assert "secret changed manifest detail" not in captured.err


def test_missing_api_key_has_safe_reason_and_next_step(monkeypatch, capsys):
    _safe_cli(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def import_missing_key(_factory, _dataset, client_factory, **_kwargs):
        client_factory()

    monkeypatch.setattr(bulk_cli, "run_import", import_missing_key)

    assert bulk_cli.main(["run", "--data", "/safe/data"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Redwood command failed: reason=missing_api_key; "
        "next_step=set OPENAI_API_KEY, then run the command again.\n"
    )


def test_reset_schema_failure_has_safe_reason_and_next_step(monkeypatch, capsys):
    _safe_cli(monkeypatch)
    monkeypatch.setattr(
        bulk_cli,
        "reset_redwood_database",
        lambda *_args: (_ for _ in ()).throw(
            RuntimeError("secret database error")
        ),
    )

    assert bulk_cli.main(["reset", "--data", "/safe/data", "--yes"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Redwood command failed: reason=schema_failure; "
        "next_step=check Redwood database access and schema, then retry; "
        "reset changes roll back.\n"
    )
    assert "secret database error" not in captured.err


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


def test_run_accepts_the_product_database_for_first_time_setup(monkeypatch):
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
        lambda *_args, **_kwargs: calls.append("run") or SimpleNamespace(
            run_id="run-1", complete=True, provider_calls=0
        ),
    )

    assert bulk_cli.main(["run", "--data", "/safe/data"]) == 0
    assert calls == ["run"]


def test_run_prints_safe_batch_progress(monkeypatch, tmp_path, capsys):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "slack.jsonl").write_bytes(b"one\ntwo\n")
    for source in ("jira", "github", "confluence"):
        (artifacts / f"{source}.jsonl").write_bytes(b"")
    _safe_cli(monkeypatch, SimpleNamespace(root=tmp_path))
    report = BatchReport(
        2,
        3,
        4,
        3,
        8,
        2,
        "slack",
        elapsed_seconds=1.25,
        cache_hits=5,
        cache_misses=6,
        concurrency=4,
        retries=1,
        sentences_per_second=3.2,
        estimated_remaining_seconds=9.5,
    )
    observed_during_run = []

    def import_with_progress(*_args, **kwargs):
        callback = kwargs.get("progress_callback")
        if callback:
            callback(report)
        observed_during_run.append(capsys.readouterr().out)
        return SimpleNamespace(
            run_id="run-1",
            complete=False,
            provider_calls=2,
            batches=(report,),
        )

    monkeypatch.setattr(bulk_cli, "run_import", import_with_progress)
    assert bulk_cli.main(["run", "--data", str(tmp_path)]) == 0
    assert observed_during_run == [
        "source=slack next_line=3 documents=2 chunks=3 sentences=4 "
        "cache_hits=5 cache_misses=6 provider_requests=2 concurrency=4 "
        "retries=1 sentences_per_second=3.20 "
        "estimated_remaining_seconds=9.50 elapsed_seconds=1.25\n"
    ]
    output = capsys.readouterr().out
    assert "source=slack" not in output
    assert "run=run-1 load_complete=no provider_calls=2" in output
    assert (
        "one" not in observed_during_run[0]
        and "two" not in observed_during_run[0]
    )


def test_resumed_run_prints_the_batch_source_not_the_first_dataset_source(
    monkeypatch, tmp_path, capsys
):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for source in ("slack", "jira", "github", "confluence"):
        (artifacts / f"{source}.jsonl").write_bytes(b"record\n")
    _safe_cli(monkeypatch, SimpleNamespace(root=tmp_path))
    def import_with_progress(*_args, **kwargs):
        report = SimpleNamespace(
            source="jira",
            documents=1,
            chunks=2,
            sentences=3,
            next_line=42,
            next_offset=999,
            provider_calls=1,
            elapsed_seconds=1.0,
            cache_hits=0,
            cache_misses=0,
            concurrency=8,
            retries=0,
            sentences_per_second=3.0,
            estimated_remaining_seconds=10.0,
        )
        callback = kwargs.get("progress_callback")
        if callback:
            callback(report)
        return SimpleNamespace(
            run_id="run-1",
            complete=False,
            provider_calls=1,
            batches=(report,),
        )

    monkeypatch.setattr(bulk_cli, "run_import", import_with_progress)
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
    def __init__(self, status="loading", updated_age=5.0):
        self.status = status
        self.updated_age = updated_age

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
                    self.status,
                    None,
                    "redwood-v1",
                    "abc123",
                    "text-embedding-3-small",
                    1536,
                    12.5,
                    self.updated_age,
                    5,
                    6,
                    7,
                    8,
                    1,
                    9.5,
                    10.5,
                    150.0,
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
        "run=run-1 status=running dataset_version=redwood-v1 "
        "manifest_digest=abc123 embedding_model=text-embedding-3-small "
        "dimensions=1536 cache_hits=5 cache_misses=6 "
        "provider_requests=7 concurrency=8 retries=1 "
        "sentences_per_second=9.50 estimated_remaining_seconds=10.50 "
        "elapsed_seconds=12.50",
        "source=jira next_line=42 documents=10 chunks=20 sentences=30",
    ]


@pytest.mark.parametrize(
    ("database_status", "updated_age", "shown_status"),
    [
        ("loading", 5.0, "running"),
        ("loading", 500.0, "stalled"),
        ("failed", 5.0, "failed"),
        ("indexing", 5.0, "indexing"),
        ("complete", 5.0, "complete"),
    ],
)
def test_status_distinguishes_operator_states(
    monkeypatch, capsys, database_status, updated_age, shown_status
):
    monkeypatch.setattr(bulk_cli, "database_url", lambda: REDWOOD_URL)
    monkeypatch.setattr(
        bulk_cli.psycopg,
        "connect",
        lambda _url: _StatusConnection(database_status, updated_age),
    )

    assert bulk_cli.main(["status"]) == 0
    assert f"status={shown_status}" in capsys.readouterr().out.splitlines()[0]


def test_run_forwards_bounded_embedding_settings(monkeypatch):
    _safe_cli(monkeypatch)
    received = []
    monkeypatch.setattr(
        bulk_cli,
        "run_import",
        lambda *_args, **kwargs: received.append(kwargs)
        or SimpleNamespace(
            run_id="run-1", complete=True, batches=(), provider_calls=0
        ),
    )

    assert bulk_cli.main(
        [
            "run",
            "--data",
            "/safe/data",
            "--work-window-size",
            "300",
            "--embedding-concurrency",
            "8",
            "--embedding-max-inputs",
            "512",
            "--embedding-max-tokens",
            "50000",
            "--embedding-connect-timeout",
            "4",
            "--embedding-read-timeout",
            "30",
            "--embedding-write-timeout",
            "20",
            "--embedding-total-timeout",
            "90",
        ]
    ) == 0
    assert received[0]["work_window_size"] == 300
    assert received[0]["request_config"] == bulk_cli.EmbeddingRequestConfig(
        concurrency=8,
        max_inputs=512,
        max_estimated_tokens=50_000,
        connect_timeout=4.0,
        read_timeout=30.0,
        write_timeout=20.0,
        total_timeout=90.0,
    )


@pytest.mark.parametrize(
    "options",
    [
        ["--work-window-size", "0"],
        ["--embedding-concurrency", "17"],
        ["--embedding-max-inputs", "2049"],
        ["--embedding-max-tokens", "300001"],
        ["--embedding-total-timeout", "0"],
        ["--embedding-total-timeout", "nan"],
    ],
)
def test_run_rejects_unsafe_settings_during_argument_parsing(options):
    with pytest.raises(SystemExit) as captured:
        bulk_cli._parser().parse_args(["run", *options])
    assert captured.value.code == 2


def test_reset_uses_one_atomic_schema_operation(monkeypatch, tmp_path):
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
        lambda _url: (_ for _ in ()).throw(
            AssertionError("second database transaction opened")
        ),
    )

    assert bulk_cli.main(["reset", "--data", str(tmp_path), "--yes"]) == 0
    assert calls == ["reset"]


def _verification_report(*, compatible=True, p95_ms=20.0):
    return VerificationReport(
        compatible=compatible,
        counts={"documents": 8, "chunks": 12, "sentences": 20},
        sources={"confluence": 1, "github": 1, "jira": 5, "slack": 1},
        missing_embeddings=0,
        acl_checks={
            "direct_user_status": "not_applicable",
            "direct_user_database_links": 0,
            "direct_user_visible": None,
            "direct_unauthorized_results": None,
            "unknown_user_results": 0,
        },
        recall_at_10=0.75,
        mrr=0.5,
        p50_ms=10.0,
        p95_ms=p95_ms,
    )


def test_verify_prints_safe_json_and_uses_released_profile(monkeypatch, capsys):
    dataset = _safe_cli(monkeypatch)
    calls = []
    monkeypatch.setattr(
        bulk_cli, "assert_import_database", lambda url: calls.append(("guard", url))
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
    assert payload["acl_checks"]["direct_user_status"] == "not_applicable"
    assert payload["acl_checks"]["direct_user_database_links"] == 0
    assert payload["acl_checks"]["direct_user_visible"] is None
    assert calls == [
        ("guard", REDWOOD_URL),
        ("verify", dataset.root, "released"),
    ]


def test_verify_returns_failure_after_printing_incompatible_report(
    monkeypatch, capsys
):
    _safe_cli(monkeypatch)
    monkeypatch.setattr(bulk_cli, "assert_import_database", lambda _url: None)
    monkeypatch.setattr(
        bulk_cli, "verify_redwood", lambda *_args: _verification_report(compatible=False)
    )
    monkeypatch.setattr(bulk_cli, "_openai_client", lambda: object())

    assert bulk_cli.main(["verify", "--data", "/safe/data", "--json"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["compatible"] is False
    assert captured.err == (
        "Redwood command failed: reason=verify_incompatible; "
        "next_step=review the safe report and run status before retrying.\n"
    )


def test_verify_returns_failure_when_p95_exceeds_two_seconds(monkeypatch, capsys):
    _safe_cli(monkeypatch)
    monkeypatch.setattr(bulk_cli, "assert_import_database", lambda _url: None)
    monkeypatch.setattr(
        bulk_cli,
        "verify_redwood",
        lambda *_args: _verification_report(compatible=True, p95_ms=2000.01),
    )
    monkeypatch.setattr(bulk_cli, "_openai_client", lambda: object())

    assert bulk_cli.main(["verify", "--data", "/safe/data", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["p95_ms"] == 2000.01


def _executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o755)
    return path


def _managed_docker(tmp_path, details):
    return _executable(
        tmp_path / "docker",
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = inspect ]; then\n"
        "  case \"$3\" in\n"
        "    *State.Running*) printf '%s\\n' \"$FAKE_DETAILS\" ;;\n"
        "    *) printf '%s\\n' 'knowledge-browser-redwood redwood-db' ;;\n"
        "  esac\n"
        "fi\n",
    )


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


@pytest.mark.parametrize("command", [("reset", "--yes"), ("run",)])
@pytest.mark.parametrize(
    "details",
    [
        "knowledge-browser-redwood|redwood-db|false|1|127.0.0.1|5433",
        "knowledge-browser-redwood|redwood-db|true|1|0.0.0.0|5433",
        "knowledge-browser-redwood|redwood-db|true|1|127.0.0.1|5434",
        (
            "knowledge-browser-redwood|redwood-db|true|2|"
            "127.0.0.1|5433|127.0.0.1|5434"
        ),
    ],
)
def test_wrapper_refuses_writes_until_exact_container_binding_is_running(
    tmp_path, command, details
):
    marker = tmp_path / "python-ran"
    docker = _managed_docker(tmp_path, details)
    python = _executable(
        tmp_path / "python",
        "#!/usr/bin/env bash\n"
        f"touch {marker}\n",
    )
    env = {
        **os.environ,
        "DOCKER_BIN": str(docker),
        "FAKE_DETAILS": details,
        "PYTHON_BIN": str(python),
        "REDWOOD_POSTGRES_PORT": "5433",
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
    assert completed.stdout == ""
    assert completed.stderr == (
        "Redwood container check failed: reason=container_mismatch; "
        "next_step=run start and check REDWOOD_POSTGRES_PORT "
        "(expected 127.0.0.1:5433).\n"
    )
    assert not marker.exists()


def test_wrapper_allows_writes_only_for_exact_running_binding(tmp_path):
    log = tmp_path / "python.log"
    details = "knowledge-browser-redwood|redwood-db|true|1|127.0.0.1|5544"
    docker = _managed_docker(tmp_path, details)
    python = _executable(
        tmp_path / "python",
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = -c ]; then\n"
        "  printf '%s\\n' "
        "'postgresql://postgres:postgres@127.0.0.1:5544/knowledge_redwood'\n"
        "  exit 0\n"
        "fi\n"
        "printf '%s\\n' \"$*\" >\"$FAKE_LOG\"\n",
    )
    env = {
        **os.environ,
        "DOCKER_BIN": str(docker),
        "FAKE_DETAILS": details,
        "FAKE_LOG": str(log),
        "PYTHON_BIN": str(python),
        "REDWOOD_POSTGRES_PORT": "5544",
    }

    completed = subprocess.run(
        ["bash", str(SCRIPT), "run", "--data", "/safe/data"],
        cwd=SCRIPT.parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert log.read_text().strip() == (
        "-m knowledge_browser.bulk_cli run --data /safe/data"
    )


def test_wrapper_start_waits_for_the_database_healthcheck(tmp_path):
    log = tmp_path / "docker.log"
    docker = _executable(
        tmp_path / "docker",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >>\"$FAKE_LOG\"\n"
        "if [ \"$1\" = inspect ]; then exit 1; fi\n",
    )
    env = {
        **os.environ,
        "DOCKER_BIN": str(docker),
        "FAKE_LOG": str(log),
    }

    completed = subprocess.run(
        ["bash", str(SCRIPT), "start"],
        cwd=SCRIPT.parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "up -d --wait --wait-timeout 60 redwood-db" in log.read_text()


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
