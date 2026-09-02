"""Validate and read the checked-in Redwood dataset."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Iterator


SOURCES = ("slack", "jira", "github", "confluence")
COUNT_KEYS = (
    "artifacts",
    "companies",
    "employees",
    "incidents",
    "projects",
    "qa",
    "teams",
)
REQUIRED_FILES = (
    "world.json",
    "employees.jsonl",
    "teams.jsonl",
    "projects.jsonl",
    "events.jsonl",
    "qa.jsonl",
    "acl.jsonl",
    *(f"artifacts/{source}.jsonl" for source in SOURCES),
)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    source: str
    kind: str
    external_id: str
    title: str
    body: str
    author: str | None
    url: str | None
    container: str | None
    created_at: str | None
    updated_at: str | None
    acl: dict[str, Any] | None
    raw_payload: dict[str, Any]
    fields: dict[str, list[str]]


@dataclass(frozen=True, slots=True)
class Dataset:
    users: tuple[dict[str, Any], ...]
    groups: tuple[dict[str, Any], ...]
    documents: tuple[ParsedDocument, ...]


@dataclass(frozen=True, slots=True)
class ValidatedDataset:
    root: Path
    manifest: dict
    manifest_digest: str
    context: dict


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    source: str
    line_number: int
    start_offset: int
    next_offset: int
    document: ParsedDocument


def _error(message: str) -> ValueError:
    return ValueError(f"invalid Redwood dataset: {message}")


def _json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise _error(f"cannot read {path.name}") from error
    if not isinstance(value, dict):
        raise _error(f"{path.name} must contain an object")
    return value


def _stream_sha256(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def _iter_jsonl(
    path: Path, *, start_offset: int = 0, start_line: int = 1
) -> Iterator[tuple[int, int, int, dict[str, Any]]]:
    try:
        with path.open("rb") as stream:
            stream.seek(start_offset)
            line_number = start_line
            while line := stream.readline():
                next_offset = stream.tell()
                current_offset = next_offset - len(line)
                if not line.strip():
                    raise _error(f"{path.name}:{line_number} must contain an object")
                try:
                    value = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    raise _error(f"invalid JSON in {path.name}:{line_number}") from error
                if not isinstance(value, dict):
                    raise _error(f"{path.name}:{line_number} must contain an object")
                yield line_number, current_offset, next_offset, value
                line_number += 1
    except OSError as error:
        raise _error(f"missing file: {path}") from error


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [record for _, _, _, record in _iter_jsonl(path)]


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise _error(f"{label} must be a non-empty string")
    return value


def _optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise _error(f"{label} must be a list of non-empty strings")
    return value


def _unique(records: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        identifier = _string(record.get(key), f"{label} {key}")
        if identifier in result:
            raise _error(f"duplicate {label} ID: {identifier}")
        result[identifier] = record
    return result


def validate_manifest(data_dir: Path) -> dict[str, Any]:
    """Return the manifest only after every listed file verifies by SHA-256."""
    root = Path(data_dir)
    if not root.is_dir():
        raise _error("data directory is missing")
    root_resolved = root.resolve()
    manifest = _json_file(root / "manifest.json")
    counts = manifest.get("counts")
    if not isinstance(counts, dict) or set(counts) != set(COUNT_KEYS):
        raise _error(f"manifest counts must contain exactly: {', '.join(COUNT_KEYS)}")
    for name, count in counts.items():
        if type(count) is not int or count < 0:
            raise _error(f"manifest count {name} must be a non-negative integer")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise _error("manifest files must be an object")
    if any(not isinstance(path, str) or not isinstance(digest, str) for path, digest in files.items()):
        raise _error("manifest files must map paths to hashes")
    for relative in files:
        relative_path = Path(relative)
        if relative_path.is_absolute() or PureWindowsPath(relative).is_absolute() or ".." in relative_path.parts:
            raise _error(f"unsafe manifest path: {relative}")
    missing = set(REQUIRED_FILES).difference(files)
    if missing:
        raise _error(f"manifest is missing required file: {sorted(missing)[0]}")
    for relative, expected_hash in files.items():
        path = root / Path(relative)
        try:
            if not path.resolve().is_relative_to(root_resolved):
                raise _error(f"unsafe manifest path: {relative}")
        except OSError as error:
            raise _error(f"unsafe manifest path: {relative}") from error
        if not path.is_file():
            raise _error(f"missing file: {relative}")
        if _stream_sha256(path) != expected_hash:
            raise _error(f"manifest hash mismatch: {relative}")

    world = _json_file(root / "world.json")
    if not isinstance(world.get("company"), dict):
        raise _error("world.json company must contain an object")
    actual_counts = {
        "artifacts": sum(
            sum(1 for _ in _iter_jsonl(root / "artifacts" / f"{source}.jsonl"))
            for source in SOURCES
        ),
        "companies": 1,
        "employees": sum(1 for _ in _iter_jsonl(root / "employees.jsonl")),
        "incidents": sum(1 for _ in _iter_jsonl(root / "events.jsonl")),
        "projects": sum(1 for _ in _iter_jsonl(root / "projects.jsonl")),
        "qa": sum(1 for _ in _iter_jsonl(root / "qa.jsonl")),
        "teams": sum(1 for _ in _iter_jsonl(root / "teams.jsonl")),
    }
    for name in COUNT_KEYS:
        if counts[name] != actual_counts[name]:
            raise _error(f"manifest count mismatch: {name}")
    return manifest


def _group_name(group_id: str) -> str:
    return group_id.removeprefix("group-").replace("-", " ").title()


def _context(root: Path) -> dict[str, Any]:
    employees = _unique(_jsonl(root / "employees.jsonl"), "id", "employee")
    emails: dict[str, dict[str, Any]] = {}
    for employee_id, employee in employees.items():
        email = _string(employee.get("email"), f"employee {employee_id} email")
        _string(employee.get("name"), f"employee {employee_id} name")
        if email in emails:
            raise _error(f"duplicate employee email: {email}")
        emails[email] = employee

    teams = _unique(_jsonl(root / "teams.jsonl"), "id", "team")
    group_names: dict[str, str] = {}
    group_payloads: dict[str, dict[str, Any]] = {}
    for team_id, team in teams.items():
        group_id = _string(team.get("acl_group_id"), f"team {team_id} acl_group_id")
        name = _string(team.get("name"), f"team {team_id} name")
        if group_id in group_names:
            raise _error(f"duplicate group ID: {group_id}")
        group_names[group_id], group_payloads[group_id] = name, team
        manager_id = _string(team.get("manager_id"), f"team {team_id} manager_id")
        if manager_id not in employees:
            raise _error(f"team {team_id} has unknown employee: {manager_id}")
        for employee_id in _strings(team.get("member_ids"), f"team {team_id} member_ids"):
            if employee_id not in employees:
                raise _error(f"team {team_id} has unknown employee: {employee_id}")

    for employee_id, employee in employees.items():
        team_id = _string(employee.get("team_id"), f"employee {employee_id} team_id")
        if team_id not in teams:
            raise _error(f"employee {employee_id} has unknown team: {team_id}")
        manager_id = employee.get("manager_id")
        if manager_id is not None and manager_id not in employees:
            raise _error(f"employee {employee_id} has unknown employee: {manager_id}")
        for group_id in _strings(employee.get("group_ids"), f"employee {employee_id} group_ids"):
            group_names.setdefault(group_id, _group_name(group_id))
            group_payloads.setdefault(group_id, {"id": group_id})
    mapped_groups: dict[str, str] = {}
    for group_id, name in group_names.items():
        if name in mapped_groups and mapped_groups[name] != group_id:
            raise _error(f"duplicate mapped group name: {name}")
        mapped_groups[name] = group_id

    projects = _unique(_jsonl(root / "projects.jsonl"), "id", "project")
    aliases: dict[str, dict[str, list[str]]] = {}
    for project_id, project in projects.items():
        if _string(project.get("owner_employee_id"), f"project {project_id} owner_employee_id") not in employees:
            raise _error(f"project {project_id} has unknown employee")
        if _string(project.get("owner_team_id"), f"project {project_id} owner_team_id") not in teams:
            raise _error(f"project {project_id} has unknown team")
        for employee_id in _strings(project.get("participant_ids"), f"project {project_id} participant_ids"):
            if employee_id not in employees:
                raise _error(f"project {project_id} has unknown employee: {employee_id}")
        shared = _strings(project.get("aliases"), f"project {project_id} aliases")
        by_source = project.get("aliases_by_source")
        if not isinstance(by_source, dict):
            raise _error(f"project {project_id} aliases_by_source must be an object")
        aliases[project_id] = {
            source: list(dict.fromkeys([*shared, *_strings(by_source.get(source, []), f"project {project_id} {source} aliases")]))
            for source in SOURCES
        }

    principals = _unique(_jsonl(root / "acl.jsonl"), "employee_id", "ACL principal")
    members = {group_id: [] for group_id in group_names}
    for employee_id, principal in principals.items():
        if employee_id not in employees:
            raise _error(f"ACL principal has unknown employee: {employee_id}")
        principal_groups = _strings(principal.get("group_ids"), f"ACL principal {employee_id} group_ids")
        if set(principal_groups) != set(_strings(employees[employee_id].get("group_ids"), f"employee {employee_id} group_ids")):
            raise _error(f"ACL groups do not match employee: {employee_id}")
        for group_id in principal_groups:
            if group_id not in group_names:
                raise _error(f"ACL principal {employee_id} has unknown group: {group_id}")
            members[group_id].append(_string(employees[employee_id].get("email"), f"employee {employee_id} email"))
    if set(principals) != set(employees):
        raise _error("ACL principals do not match employees")

    return {
        "employees": employees,
        "emails": {employee_id: employee["email"] for employee_id, employee in employees.items()},
        "names": {employee_id: employee["name"] for employee_id, employee in employees.items()},
        "groups": group_names,
        "projects": projects,
        "aliases": aliases,
        "users": tuple({"email": employee["email"], "name": employee["name"], "raw_payload": employee} for employee in employees.values()),
        "identity_groups": tuple({"name": name, "members": sorted(members[group_id]), "raw_payload": group_payloads[group_id]} for group_id, name in group_names.items()),
    }


def _add_field(fields: dict[str, list[str]], name: str, values: list[str]) -> None:
    if values := [value for value in values if value]:
        fields[name] = values


def _without_nuls(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "\ufffd")
    if isinstance(value, list):
        return [_without_nuls(item) for item in value]
    if isinstance(value, dict):
        return {
            _without_nuls(key): _without_nuls(item)
            for key, item in value.items()
        }
    return value


def _employee_terms(employee_ids: list[str], artifact_id: str, context: dict[str, Any]) -> list[str]:
    values = []
    for employee_id in employee_ids:
        if employee_id not in context["employees"]:
            raise _error(f"artifact {artifact_id} has unknown employee: {employee_id}")
        values.extend([context["names"][employee_id], context["emails"][employee_id]])
    return values


def _mapped_acl(value: Any, context: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    company_access, group_ids, user_ids = value.get("company_access"), value.get("group_ids"), value.get("user_ids")
    if type(company_access) is not bool or not isinstance(group_ids, list) or not isinstance(user_ids, list):
        return None
    if any(not isinstance(group_id, str) or group_id not in context["groups"] for group_id in group_ids):
        return None
    if any(not isinstance(user_id, str) or user_id not in context["emails"] for user_id in user_ids):
        return None
    acl: dict[str, Any] = {}
    if company_access:
        acl["company"] = True
    if group_ids:
        acl["groups"] = [context["groups"][group_id] for group_id in group_ids]
    if user_ids:
        acl["users"] = [context["emails"][user_id] for user_id in user_ids]
    return acl


def _project_aliases(artifact: dict[str, Any], source: str, context: dict[str, Any]) -> list[str]:
    artifact_id = _string(artifact.get("id"), "artifact id")
    project_ids = _strings(artifact.get("project_ids"), f"artifact {artifact_id} project_ids")
    if not project_ids:
        raise _error(f"artifact {artifact_id} must reference a project")
    aliases: list[str] = []
    for project_id in project_ids:
        if project_id not in context["projects"]:
            raise _error(f"artifact {artifact_id} has unknown project: {project_id}")
        aliases.extend(context["aliases"][project_id][source])
    return list(dict.fromkeys(aliases))


def _document(artifact: dict[str, Any], source: str, context: dict[str, Any]) -> ParsedDocument:
    artifact_id = _string(artifact.get("id"), "artifact id")
    if artifact.get("source") != source:
        raise _error(f"artifact {artifact_id} source does not match its file")
    author_id = _string(artifact.get("author_id"), f"artifact {artifact_id} author_id")
    if author_id not in context["employees"]:
        raise _error(f"artifact {artifact_id} has unknown employee: {author_id}")
    kind = _string(artifact.get("kind"), f"artifact {artifact_id} kind")
    payload = artifact.get("payload")
    if not isinstance(payload, dict):
        raise _error(f"artifact {artifact_id} payload must be an object")
    fields: dict[str, list[str]] = {}
    _add_field(fields, "project_alias", _project_aliases(artifact, source, context))
    _add_field(fields, "artifact_kind", [kind])

    if source == "slack":
        channel = _string(payload.get("channel"), f"artifact {artifact_id} channel")
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise _error(f"artifact {artifact_id} messages must be a list")
        texts, timestamps = [], []
        for message in messages:
            if not isinstance(message, dict):
                raise _error(f"artifact {artifact_id} message must be an object")
            message_author = _string(message.get("author_id"), f"artifact {artifact_id} message author_id")
            if message_author not in context["employees"]:
                raise _error(f"artifact {artifact_id} has unknown employee: {message_author}")
            texts.append(_string(message.get("text"), f"artifact {artifact_id} message text"))
            timestamps.append(_string(message.get("timestamp"), f"artifact {artifact_id} message timestamp"))
        _add_field(fields, "channel", [channel])
        _add_field(fields, "message", texts)
        _add_field(fields, "message_timestamp", timestamps)
        _add_field(fields, "mention", _employee_terms(_strings(payload.get("mention_ids"), f"artifact {artifact_id} mention_ids"), artifact_id, context))
        title, body, container, document_kind = (texts[0] if texts else channel), "\n".join(texts), channel, "slack_message"
    elif source == "jira":
        reporter = _string(payload.get("reporter_id"), f"artifact {artifact_id} reporter_id")
        assignee = _string(payload.get("assignee_id"), f"artifact {artifact_id} assignee_id")
        issue_key = _string(payload.get("issue_key"), f"artifact {artifact_id} issue_key")
        summary = _string(payload.get("summary"), f"artifact {artifact_id} summary")
        description = _string(payload.get("description"), f"artifact {artifact_id} description")
        history = payload.get("status_history")
        if not isinstance(history, list):
            raise _error(f"artifact {artifact_id} status_history must be a list")
        statuses = []
        for change in history:
            if not isinstance(change, dict):
                raise _error(f"artifact {artifact_id} status history entry must be an object")
            actor = _string(change.get("actor_id"), f"artifact {artifact_id} status actor_id")
            if actor not in context["employees"]:
                raise _error(f"artifact {artifact_id} has unknown employee: {actor}")
            statuses.append(f"{_string(change.get('status'), f'artifact {artifact_id} status')} ({_string(change.get('timestamp'), f'artifact {artifact_id} status timestamp')})")
        if statuses:
            _add_field(fields, "issue_metadata", [f"{issue_key} final status {history[-1]['status']}"])
        _add_field(fields, "summary", [summary])
        _add_field(fields, "description", [description])
        _add_field(fields, "reporter", _employee_terms([reporter], artifact_id, context))
        _add_field(fields, "assignee", _employee_terms([assignee], artifact_id, context))
        _add_field(fields, "label", _strings(payload.get("labels", []), f"artifact {artifact_id} labels"))
        _add_field(fields, "component", _strings(payload.get("components", []), f"artifact {artifact_id} components"))
        _add_field(fields, "affected_version", _strings(payload.get("affected_versions", []), f"artifact {artifact_id} affected_versions"))
        _add_field(fields, "fix_version", _strings(payload.get("fix_versions", []), f"artifact {artifact_id} fix_versions"))
        _add_field(fields, "status_history", statuses)
        _add_field(fields, "comment", _strings(payload.get("comments"), f"artifact {artifact_id} comments"))
        title, body, container, document_kind = summary, description, artifact["project_ids"][0], "jira_issue"
    elif source == "github":
        title = _string(payload.get("title"), f"artifact {artifact_id} title")
        body = _string(payload.get("body"), f"artifact {artifact_id} body")
        review_state = _string(payload.get("review_state"), f"artifact {artifact_id} review_state")
        record_type = _string(payload.get("record_type"), f"artifact {artifact_id} record_type")
        container = _string(payload.get("repository"), f"artifact {artifact_id} repository")
        references = [f"#{payload['number']}"] if isinstance(payload.get("number"), int) else []
        references.extend(_strings(payload.get("commit_ids"), f"artifact {artifact_id} commit_ids"))
        _add_field(fields, "title", [title])
        _add_field(fields, "body", [body])
        _add_field(fields, "review_state", [review_state])
        _add_field(fields, "reviewer", _employee_terms(_strings(payload.get("reviewer_ids", []), f"artifact {artifact_id} reviewer_ids"), artifact_id, context))
        if payload.get("merge_version") is not None:
            _add_field(fields, "merge_version", [_string(payload["merge_version"], f"artifact {artifact_id} merge_version")])
        _add_field(fields, "reference", references)
        document_kind = f"github_{record_type}"
    else:
        title = _string(payload.get("page_title"), f"artifact {artifact_id} page_title")
        sections = payload.get("sections")
        if not isinstance(sections, list):
            raise _error(f"artifact {artifact_id} sections must be a list")
        headings, bodies = [], []
        for section in sections:
            if not isinstance(section, dict):
                raise _error(f"artifact {artifact_id} section must be an object")
            headings.append(_string(section.get("heading"), f"artifact {artifact_id} section heading"))
            bodies.append(_string(section.get("body"), f"artifact {artifact_id} section body"))
        container = _string(payload.get("space"), f"artifact {artifact_id} space")
        page_status = _string(payload.get("page_status"), f"artifact {artifact_id} page_status")
        version = payload.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise _error(f"artifact {artifact_id} version must be an integer")
        _add_field(fields, "title", [title])
        _add_field(fields, "page_status", [page_status])
        _add_field(fields, "space", [container])
        _add_field(fields, "version", [str(version)])
        _add_field(fields, "section_heading", headings)
        _add_field(fields, "section_body", bodies)
        body, document_kind = "\n".join(bodies), "confluence_page"

    return ParsedDocument(
        source=_without_nuls(source),
        kind=_without_nuls(document_kind),
        external_id=_without_nuls(artifact_id),
        title=_without_nuls(title),
        body=_without_nuls(body),
        author=_without_nuls(context["names"][author_id]),
        url=_without_nuls(f"https://synthetic.local/{source}/{artifact_id}"),
        container=_without_nuls(container),
        created_at=_optional_string(
            artifact.get("created_at"), f"artifact {artifact_id} created_at"
        ),
        updated_at=_optional_string(
            artifact.get("updated_at"), f"artifact {artifact_id} updated_at"
        ),
        acl=_without_nuls(_mapped_acl(artifact.get("acl"), context)),
        raw_payload=_without_nuls(artifact),
        fields=_without_nuls(fields),
    )


def validate_streaming_dataset(data_dir: Path) -> ValidatedDataset:
    """Validate the dataset without retaining its artifacts in memory."""
    root = Path(data_dir)
    manifest = validate_manifest(root)
    context = _context(root)
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("CREATE TEMPORARY TABLE artifact_ids (external_id TEXT PRIMARY KEY)")
        for source in SOURCES:
            for _, _, _, artifact in _iter_jsonl(root / "artifacts" / f"{source}.jsonl"):
                document = _document(artifact, source, context)
                try:
                    connection.execute(
                        "INSERT INTO artifact_ids (external_id) VALUES (?)",
                        (document.external_id,),
                    )
                except sqlite3.IntegrityError as error:
                    raise ValueError("duplicate artifact ID") from error
    return ValidatedDataset(root, manifest, _stream_sha256(root / "manifest.json"), context)


def iter_artifacts(
    dataset: ValidatedDataset,
    source: str,
    start_offset: int = 0,
    start_line: int = 1,
) -> Iterator[ArtifactRecord]:
    """Yield source artifacts from a saved byte offset and line number."""
    if source not in SOURCES:
        raise _error(f"unknown artifact source: {source}")
    for line_number, current_offset, next_offset, artifact in _iter_jsonl(
        dataset.root / "artifacts" / f"{source}.jsonl",
        start_offset=start_offset,
        start_line=start_line,
    ):
        yield ArtifactRecord(
            source,
            line_number,
            current_offset,
            next_offset,
            _document(artifact, source, dataset.context),
        )


def load_dataset(data_dir: Path) -> Dataset:
    """Load the manifest-verified canonical dataset into importer-ready records."""
    dataset = validate_streaming_dataset(data_dir)
    documents = tuple(
        record.document
        for source in SOURCES
        for record in iter_artifacts(dataset, source)
    )
    return Dataset(dataset.context["users"], dataset.context["identity_groups"], documents)
