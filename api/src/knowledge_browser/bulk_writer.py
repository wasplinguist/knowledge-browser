"""Deterministic batched writes for bulk-import identities and ACLs."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import batched
from typing import Any, Iterable
from uuid import UUID, uuid5

from psycopg.types.json import Jsonb

from .importer import _acl_key


NAMESPACE = UUID("5f975176-6ea4-4f55-a1f8-b04f0ec25112")


@dataclass(frozen=True, slots=True)
class IdentityMaps:
    users: dict[str, UUID]
    groups: dict[str, UUID]


def stable_uuid(kind: str, key: str) -> UUID:
    return uuid5(NAMESPACE, f"{kind}:{key}")


def _executemany(
    conn, statement: str, rows: Iterable[tuple], page_size: int = 1000
) -> None:
    with conn.cursor() as cursor:
        for page in batched(rows, page_size):
            cursor.executemany(statement, page)


def _identity_map(
    conn, table: str, column: str, keys: list[str]
) -> dict[str, UUID]:
    if not keys:
        return {}
    return dict(
        conn.execute(
            f"SELECT {column}, id FROM public.{table} WHERE {column} = ANY(%s)",
            (keys,),
        ).fetchall()
    )


def import_identities(conn, context: dict[str, Any], page_size: int = 1000) -> IdentityMaps:
    """Insert validated dataset identities and return their deterministic maps."""
    if page_size < 1:
        raise ValueError("page_size must be positive")
    users = context["users"]
    groups = context["identity_groups"]
    _executemany(
        conn,
        """
        INSERT INTO public.users (id, email, name, raw_payload)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (email) DO NOTHING
        """,
        (
            (
                stable_uuid("user", user["email"]),
                user["email"],
                user["name"],
                Jsonb(user["raw_payload"]),
            )
            for user in users
        ),
        page_size,
    )
    _executemany(
        conn,
        """
        INSERT INTO public.groups (id, name, raw_payload)
        VALUES (%s, %s, %s)
        ON CONFLICT (name) DO NOTHING
        """,
        (
            (
                stable_uuid("group", group["name"]),
                group["name"],
                Jsonb(group["raw_payload"]),
            )
            for group in groups
        ),
        page_size,
    )
    identities = IdentityMaps(
        _identity_map(conn, "users", "email", [user["email"] for user in users]),
        _identity_map(conn, "groups", "name", [group["name"] for group in groups]),
    )
    membership_rows = []
    for group in groups:
        for email in group["members"]:
            try:
                membership_rows.append(
                    (identities.groups[group["name"]], identities.users[email])
                )
            except KeyError as error:
                raise ValueError(f"group has unknown user: {email}") from error
    _executemany(
        conn,
        """
        INSERT INTO public.group_memberships (group_id, user_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        membership_rows,
        page_size,
    )
    return identities


def permission_id(acl: dict[str, Any] | None) -> UUID:
    _, digest = _acl_key(acl)
    return stable_uuid("permission", digest)


def ensure_permissions(
    conn, acls: Iterable[dict[str, Any] | None], identities: IdentityMaps
) -> None:
    """Insert every distinct ACL and its direct user/group links."""
    permissions: dict[UUID, tuple[dict[str, Any], str]] = {}
    for acl in acls:
        normalized, digest = _acl_key(acl)
        permissions.setdefault(stable_uuid("permission", digest), (normalized, digest))

    _executemany(
        conn,
        """
        INSERT INTO public.permission_sets (id, visibility, raw_payload)
        VALUES (%s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (
            (
                identifier,
                "company" if acl.get("company") else "restricted",
                Jsonb({"key": digest, **acl}),
            )
            for identifier, (acl, digest) in permissions.items()
        ),
    )
    user_links, group_links = [], []
    for identifier, (acl, _) in permissions.items():
        for email in acl.get("users", []):
            try:
                user_links.append((identifier, identities.users[email]))
            except KeyError as error:
                raise ValueError(f"ACL has unknown user: {email}") from error
        for name in acl.get("groups", []):
            try:
                group_links.append((identifier, identities.groups[name]))
            except KeyError as error:
                raise ValueError(f"ACL has unknown group: {name}") from error
    _executemany(
        conn,
        """
        INSERT INTO public.permission_set_users (permission_set_id, user_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        user_links,
    )
    _executemany(
        conn,
        """
        INSERT INTO public.permission_set_groups (permission_set_id, group_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        group_links,
    )
