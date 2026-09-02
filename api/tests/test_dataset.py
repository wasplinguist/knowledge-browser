import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from knowledge_browser.dataset import load_dataset, validate_manifest


DATASET = Path(__file__).parents[2] / "data" / "redwood"
pytestmark = pytest.mark.unit

EXPECTED_COUNTS = {
    "artifacts": 13214,
    "companies": 1,
    "employees": 7245,
    "incidents": 0,
    "projects": 12,
    "qa": 274,
    "teams": 12,
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

    assert manifest["counts"]["artifacts"] == 13214
    assert len(dataset.users) == 7245
    assert len(dataset.documents) == 13214
    assert Counter(item.source for item in dataset.documents) == {
        "confluence": 1904,
        "github": 3825,
        "jira": 3303,
        "slack": 4182,
    }

    slack = next(
        item
        for item in dataset.documents
        if item.external_id == "dsid_0767a662eacd463aaf0935750dba509e"
    )
    assert slack.acl == {"company": True}
    assert "Infrastructure" in slack.fields["project_alias"]
    assert slack.fields["channel"] == ["eng-infra"]


@pytest.mark.parametrize("count_name", EXPECTED_COUNTS)
def test_manifest_rejects_each_declared_count_mismatch(tmp_path: Path, count_name: str):
    copied = shutil.copytree(DATASET, tmp_path / "redwood")
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
    copied = shutil.copytree(DATASET, tmp_path / "redwood")
    _replace_manifest(
        copied,
        lambda manifest: manifest["counts"].__setitem__("employees", invalid_count),
    )

    with pytest.raises(ValueError, match="manifest count employees must be a non-negative integer"):
        validate_manifest(copied)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_manifest_rejects_noncanonical_count_keys(tmp_path: Path, mutation: str):
    copied = shutil.copytree(DATASET, tmp_path / "redwood")

    def mutate(manifest):
        if mutation == "missing":
            manifest["counts"].pop("qa")
        else:
            manifest["counts"]["evidence_graphs"] = 603

    _replace_manifest(copied, mutate)

    with pytest.raises(ValueError, match="manifest counts must contain exactly"):
        validate_manifest(copied)


def test_changed_bytes_fail(tmp_path: Path):
    copied = shutil.copytree(DATASET, tmp_path / "redwood")
    jira = copied / "artifacts" / "jira.jsonl"
    jira.write_bytes(jira.read_bytes() + b" ")

    with pytest.raises(ValueError, match="manifest hash mismatch"):
        validate_manifest(copied)


def test_invalid_acl_is_hidden(tmp_path: Path):
    copied = shutil.copytree(DATASET, tmp_path / "redwood")
    records = _records(copied / "artifacts" / "slack.jsonl")
    records[0]["acl"] = {"company_access": "yes", "group_ids": [], "user_ids": []}
    _replace_records(copied, "artifacts/slack.jsonl", records)

    document = next(item for item in load_dataset(copied).documents if item.external_id == records[0]["id"])
    assert document.acl is None


def test_nul_in_artifact_text_is_normalized_before_database_ingestion(tmp_path: Path):
    copied = shutil.copytree(DATASET, tmp_path / "redwood")
    records = _records(copied / "artifacts" / "slack.jsonl")
    records[0]["payload"]["messages"][0]["text"] = "before\x00after"
    _replace_records(copied, "artifacts/slack.jsonl", records)

    document = next(
        item
        for item in load_dataset(copied).documents
        if item.external_id == records[0]["id"]
    )

    assert document.title == "before\ufffdafter"
    assert document.fields["message"][0] == "before\ufffdafter"
    assert document.raw_payload["payload"]["messages"][0]["text"] == "before\ufffdafter"


def test_reader_rejects_unsafe_manifest_paths_and_unknown_artifact_references(tmp_path: Path):
    copied = shutil.copytree(DATASET, tmp_path / "redwood")
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
    copied = shutil.copytree(DATASET, tmp_path / "redwood")
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"not": "dataset data"}\n', encoding="utf-8")
    (copied / "outside.jsonl").symlink_to(outside)
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["outside.jsonl"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe manifest path"):
        validate_manifest(copied)
