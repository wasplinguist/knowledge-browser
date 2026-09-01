import hashlib
import json
import shutil
from pathlib import Path

import pytest

from knowledge_browser.dataset import iter_artifacts, validate_streaming_dataset


DATASET = Path(__file__).parents[2] / "data" / "company"
pytestmark = pytest.mark.unit


def _replace_records(data_dir: Path, relative_path: str, records: list[dict]) -> None:
    path = data_dir / relative_path
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    manifest_path = data_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][relative_path] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")


def test_streaming_validation_does_not_read_whole_artifact_files(monkeypatch):
    original = Path.read_bytes

    def guarded(path):
        if "artifacts" in path.parts:
            raise AssertionError("artifact files must be streamed")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)

    assert validate_streaming_dataset(DATASET).manifest["counts"]["artifacts"] == 1000


def test_iterator_resumes_at_saved_offset():
    validated = validate_streaming_dataset(DATASET)
    records = iter_artifacts(validated, "jira")
    first, second = next(records), next(records)

    resumed = next(iter_artifacts(validated, "jira", first.next_offset, second.line_number))

    assert resumed.document.external_id == second.document.external_id


def test_streaming_validation_rejects_duplicate_artifact_ids_across_sources(tmp_path: Path):
    copied = shutil.copytree(DATASET, tmp_path / "company")
    jira = json.loads((copied / "artifacts" / "jira.jsonl").read_text(encoding="utf-8").splitlines()[0])
    github_path = copied / "artifacts" / "github.jsonl"
    github = [json.loads(line) for line in github_path.read_text(encoding="utf-8").splitlines()]
    github[0]["id"] = jira["id"]
    _replace_records(copied, "artifacts/github.jsonl", github)

    with pytest.raises(ValueError, match="duplicate artifact ID"):
        validate_streaming_dataset(copied)
