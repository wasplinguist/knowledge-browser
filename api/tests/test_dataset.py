import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from knowledge_browser.dataset import load_dataset, validate_manifest


DATASET = Path(__file__).parents[2] / "data" / "company"
pytestmark = pytest.mark.unit


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _replace_records(data_dir: Path, relative_path: str, records: list[dict]) -> None:
    path = data_dir / relative_path
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    manifest_path = data_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][relative_path] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")


def test_canonical_counts_and_source_fields():
    manifest = validate_manifest(DATASET)
    dataset = load_dataset(DATASET)

    assert manifest["counts"]["artifacts"] == 1000
    assert len(dataset.users) == 100
    assert len(dataset.documents) == 1000
    assert Counter(item.source for item in dataset.documents) == {
        "confluence": 250,
        "github": 250,
        "jira": 250,
        "slack": 250,
    }

    restricted = next(
        item for item in dataset.documents if item.external_id == "artifact-001-confluence-postmortem"
    )
    jira = next(item for item in dataset.documents if item.external_id == "artifact-001-jira-issue")
    assert restricted.acl == {"groups": ["Product Platform"]}
    assert jira.fields["project_alias"]
    assert jira.fields["issue_metadata"] == ["NIMREL-401 final status Resolved"]


def test_changed_bytes_fail(tmp_path: Path):
    copied = shutil.copytree(DATASET, tmp_path / "company")
    jira = copied / "artifacts" / "jira.jsonl"
    jira.write_bytes(jira.read_bytes() + b" ")

    with pytest.raises(ValueError, match="manifest hash mismatch"):
        validate_manifest(copied)


def test_invalid_acl_is_hidden(tmp_path: Path):
    copied = shutil.copytree(DATASET, tmp_path / "company")
    records = _records(copied / "artifacts" / "slack.jsonl")
    records[0]["acl"] = {"company_access": "yes", "group_ids": [], "user_ids": []}
    _replace_records(copied, "artifacts/slack.jsonl", records)

    document = next(item for item in load_dataset(copied).documents if item.external_id == records[0]["id"])
    assert document.acl is None


def test_reader_rejects_unsafe_manifest_paths_and_unknown_artifact_references(tmp_path: Path):
    copied = shutil.copytree(DATASET, tmp_path / "company")
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = manifest["files"].pop("employees.jsonl")
    manifest["files"]["../employees.jsonl"] = digest
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe manifest path"):
        validate_manifest(copied)

    copied = shutil.copytree(DATASET, tmp_path / "bad-reference")
    records = _records(copied / "artifacts" / "jira.jsonl")
    records[0]["payload"]["reporter_id"] = "emp-missing"
    _replace_records(copied, "artifacts/jira.jsonl", records)

    with pytest.raises(ValueError, match="unknown employee"):
        load_dataset(copied)


def test_manifest_rejects_symlinked_files_outside_the_dataset(tmp_path: Path):
    copied = shutil.copytree(DATASET, tmp_path / "company")
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"not": "dataset data"}\n', encoding="utf-8")
    (copied / "outside.jsonl").symlink_to(outside)
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["outside.jsonl"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe manifest path"):
        validate_manifest(copied)
