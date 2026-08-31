"""Independent ACL oracle for evaluation; never imports production ACL SQL."""

from collections.abc import Mapping
from collections.abc import Callable, Sequence
from typing import Any
from uuid import UUID


def is_visible(
    permission: Mapping[str, Any] | None,
    user_id: UUID,
    group_ids: set[UUID],
) -> bool:
    if not isinstance(permission, Mapping):
        return False
    if permission.get("visibility") == "company":
        return True
    if permission.get("visibility") != "restricted":
        return False
    return user_id in permission.get("users", set()) or bool(
        group_ids.intersection(permission.get("groups", set()))
    )


def allowed_documents(
    documents: Mapping[str, Mapping[str, Any] | None],
    user_id: UUID,
    group_ids: set[UUID],
) -> set[str]:
    return {
        document_id
        for document_id, permission in documents.items()
        if is_visible(permission, user_id, group_ids)
    }


def entitlement_snapshot(conn) -> tuple[
    dict[UUID, set[UUID]], dict[str, Mapping[str, Any]]
]:
    """Read raw ACL relations and rebuild expected access without production SQL."""
    memberships: dict[UUID, set[UUID]] = {
        user_id: set()
        for (user_id,) in conn.execute("SELECT id FROM users").fetchall()
    }
    for user_id, group_id in conn.execute(
        "SELECT user_id, group_id FROM group_memberships"
    ).fetchall():
        memberships.setdefault(user_id, set()).add(group_id)

    permissions: dict[UUID, dict[str, Any]] = {
        permission_id: {"visibility": visibility, "users": set(), "groups": set()}
        for permission_id, visibility in conn.execute(
            "SELECT id, visibility FROM permission_sets"
        ).fetchall()
    }
    for permission_id, user_id in conn.execute(
        "SELECT permission_set_id, user_id FROM permission_set_users"
    ).fetchall():
        if permission_id in permissions:
            permissions[permission_id]["users"].add(user_id)
    for permission_id, group_id in conn.execute(
        "SELECT permission_set_id, group_id FROM permission_set_groups"
    ).fetchall():
        if permission_id in permissions:
            permissions[permission_id]["groups"].add(group_id)

    documents = {
        f"{source}:{external_id}": permissions.get(permission_id)
        for source, external_id, permission_id in conn.execute(
            "SELECT source, external_id, permission_set_id FROM documents"
        ).fetchall()
    }
    return memberships, documents


def audit_acl(
    memberships: Mapping[UUID, set[UUID]],
    documents: Mapping[str, Mapping[str, Any] | None],
    queries: Sequence[str],
    search: Callable[[UUID, str], Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    root_leaks: list[dict[str, str]] = []
    child_leaks: list[dict[str, str]] = []
    for user_id, group_ids in memberships.items():
        expected = allowed_documents(documents, user_id, group_ids)
        for query in queries:
            for item in search(user_id, query):
                root = f'{item["source"]}:{item["external_id"]}'
                child = f'{item["source"]}:{item["matched_external_id"]}'
                common = {"user_id": str(user_id), "query": query}
                if root not in expected:
                    root_leaks.append({**common, "document": root})
                if child not in expected:
                    child_leaks.append({**common, "document": child})
    return {
        "pairs": len(memberships) * len(queries),
        "root_leaks": root_leaks,
        "child_leaks": child_leaks,
    }
