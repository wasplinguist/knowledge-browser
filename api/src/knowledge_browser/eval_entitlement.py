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


def _permission_key(permission: Mapping[str, Any] | None):
    if not isinstance(permission, Mapping):
        return None
    return (
        permission.get("visibility"),
        frozenset(permission.get("users", ())),
        frozenset(permission.get("groups", ())),
    )


def entitlement_classes(
    memberships: Mapping[UUID, set[UUID]],
    documents: Mapping[str, Mapping[str, Any] | None],
    distinguish: Mapping[UUID, Any] | None = None,
) -> dict[UUID, tuple[UUID, ...]]:
    """Group users that search cannot tell apart, so one stands in for all.

    A user reaches a document only through its permission set, so users who
    resolve the same permission sets share one allowed document set. Ranking can
    still read the user directly — personalization boosts the user's own project
    — and that would let one member of a class retrieve a document another
    member truncates away. Pass those ranking inputs as `distinguish` (user id
    to whatever the profile reads) so such users stay in separate classes;
    omitting it is only safe when no ranking signal reads the user.
    """
    permissions = {
        key: permission
        for permission in documents.values()
        if (key := _permission_key(permission)) is not None
    }
    ordered = sorted(permissions, key=repr)
    classes: dict[tuple[Any, ...], list[UUID]] = {}
    for user_id, group_ids in memberships.items():
        signature = (
            tuple(is_visible(permissions[key], user_id, group_ids) for key in ordered),
            repr((distinguish or {}).get(user_id)),
        )
        classes.setdefault(signature, []).append(user_id)
    return {min(members): tuple(sorted(members)) for members in classes.values()}


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
    """Search every pair, report leaks, and count what the search retrieved.

    `pairs` counts attempts, not hits, so zero leaks is also what a search that
    returns nothing reports. `restricted_hits` is the caller's guard: a gate that
    asserts it stays positive fails when the retrieval it audits goes inert,
    which is how a zero query vector emptied these result sets before.
    """
    restricted = {
        document_id
        for document_id, permission in documents.items()
        if isinstance(permission, Mapping)
        and permission.get("visibility") != "company"
    }
    root_leaks: list[dict[str, str]] = []
    child_leaks: list[dict[str, str]] = []
    hits = 0
    restricted_hits = 0
    for user_id, group_ids in memberships.items():
        expected = allowed_documents(documents, user_id, group_ids)
        for query in queries:
            for item in search(user_id, query):
                root = f'{item["source"]}:{item["external_id"]}'
                child = f'{item["source"]}:{item["matched_external_id"]}'
                hits += 1
                if not {root, child}.isdisjoint(restricted):
                    restricted_hits += 1
                common = {"user_id": str(user_id), "query": query}
                if root not in expected:
                    root_leaks.append({**common, "document": root})
                if child not in expected:
                    child_leaks.append({**common, "document": child})
    return {
        "pairs": len(memberships) * len(queries),
        "hits": hits,
        "restricted_hits": restricted_hits,
        "root_leaks": root_leaks,
        "child_leaks": child_leaks,
    }
