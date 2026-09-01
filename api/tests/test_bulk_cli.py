import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import knowledge_browser.bulk_cli as bulk_cli
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
            batches=(BatchReport(2, 3, 4, 3, 8, 2),),
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


def test_status_never_creates_embedding_client(monkeypatch):
    monkeypatch.setattr(bulk_cli, "database_url", lambda: REDWOOD_URL)
    monkeypatch.setattr(bulk_cli, "_print_status", lambda _url: None)
    monkeypatch.setattr(
        bulk_cli,
        "_openai_client",
        lambda: (_ for _ in ()).throw(AssertionError("client created")),
    )

    assert bulk_cli.main(["status"]) == 0


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
