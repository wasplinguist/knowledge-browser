from dataclasses import asdict
from typing import Any

from .repository import get_document


PAYLOAD_FIELDS = {
    "jira": {
        "issue_key", "project", "issue_type", "status", "summary",
        "description", "assignee", "priority", "labels", "components",
        "affected_versions", "fix_versions", "comments", "status_history",
    },
    "confluence": {
        "space", "page_status", "status", "version", "page_title", "title",
        "body", "sections", "labels", "comments",
    },
    "slack": {
        "workspace", "channel", "title", "text", "messages", "replies",
    },
    "github": {
        "repository", "record_type", "type", "number", "review_state",
        "state", "title", "body", "commit_ids", "merge_version", "labels",
        "reviews", "comments",
    },
}


def get_document_detail(
    conn, user_id: str, source: str, external_id: str
) -> dict[str, Any] | None:
    document = get_document(conn, user_id, source, external_id)
    if document is None:
        return None

    raw_payload = document.raw_payload or {}
    source_payload = raw_payload.get("payload", raw_payload)
    if not isinstance(source_payload, dict):
        source_payload = {}
    payload = {
        key: source_payload[key]
        for key in PAYLOAD_FIELDS[source]
        if key in source_payload
    }
    common = asdict(document)
    return {
        "source": common["source"],
        "external_id": common["external_id"],
        "kind": common["kind"],
        "title": common["title"],
        "author": common["author"],
        "container": common["container"],
        "created_at": common["source_created_at"],
        "updated_at": common["source_updated_at"],
        "payload": payload,
    }
