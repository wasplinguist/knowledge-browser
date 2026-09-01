import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from knowledge_browser.dataset import load_dataset, validate_manifest


DATASET = Path(__file__).parents[2] / "data" / "company"
pytestmark = pytest.mark.unit

EXPECTED_COUNTS = {
    "artifacts": 1000,
    "companies": 1,
    "employees": 100,
    "incidents": 125,
    "projects": 25,
    "qa": 603,
    "teams": 10,
}


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _replace_records(data_dir: Path, relative_path: str, records: list[dict]) -> None:
    path = data_dir / relative_path
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    manifest_path = data_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][relative_path] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")


def _replace_manifest(data_dir: Path, mutate) -> None:
    manifest_path = data_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutate(manifest)
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


@pytest.mark.parametrize("count_name", EXPECTED_COUNTS)
def test_manifest_rejects_each_declared_count_mismatch(tmp_path: Path, count_name: str):
    copied = shutil.copytree(DATASET, tmp_path / "company")
    _replace_manifest(
        copied,
        lambda manifest: manifest["counts"].__setitem__(
            count_name, EXPECTED_COUNTS[count_name] + 1
        ),
    )

    with pytest.raises(ValueError, match=f"manifest count mismatch: {count_name}"):
        validate_manifest(copied)


@pytest.mark.parametrize("invalid_count", [True, "100", -1])
def test_manifest_rejects_invalid_count_values(tmp_path: Path, invalid_count):
    copied = shutil.copytree(DATASET, tmp_path / "company")
    _replace_manifest(
        copied,
        lambda manifest: manifest["counts"].__setitem__("employees", invalid_count),
    )

    with pytest.raises(ValueError, match="manifest count employees must be a non-negative integer"):
        validate_manifest(copied)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_manifest_rejects_noncanonical_count_keys(tmp_path: Path, mutation: str):
    copied = shutil.copytree(DATASET, tmp_path / "company")

    def mutate(manifest):
        if mutation == "missing":
            manifest["counts"].pop("qa")
        else:
            manifest["counts"]["evidence_graphs"] = 603

    _replace_manifest(copied, mutate)

    with pytest.raises(ValueError, match="manifest counts must contain exactly"):
        validate_manifest(copied)


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
