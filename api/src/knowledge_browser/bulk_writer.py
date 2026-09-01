"""Deterministic batched writes for bulk-import identities and ACLs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from itertools import batched
from typing import Any, Iterable
from uuid import UUID, uuid5

from psycopg.types.json import Jsonb

from .embedding_index import encoded_vector, sentences
from .importer import _acl_key


NAMESPACE = UUID("5f975176-6ea4-4f55-a1f8-b04f0ec25112")


@dataclass(frozen=True, slots=True)
class IdentityMaps:
    users: dict[str, UUID]
    groups: dict[str, UUID]


@dataclass(frozen=True, slots=True)
class BatchReport:
    documents: int
    chunks: int
    sentences: int
    next_line: int
    next_offset: int
    provider_calls: int
    source: str
    elapsed_seconds: float = 0.0


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


def _document_matches(conn, document, document_id, permission_set_id):
    row = conn.execute(
        """
        SELECT id = %s
           AND kind = %s
           AND parent_document_id IS NULL
           AND root_document_id = %s
           AND permission_set_id = %s
           AND title = %s
           AND body = %s
           AND author IS NOT DISTINCT FROM %s
           AND url IS NOT DISTINCT FROM %s
           AND container IS NOT DISTINCT FROM %s
           AND raw_payload = %s
           AND source_created_at IS NOT DISTINCT FROM %s::timestamptz
           AND source_updated_at IS NOT DISTINCT FROM %s::timestamptz
        FROM public.documents
        WHERE source = %s AND external_id = %s
        """,
        (
            document_id,
            document.kind,
            document_id,
            permission_set_id,
            document.title,
            document.body,
            document.author,
            document.url,
            document.container,
            Jsonb(document.raw_payload),
            document.created_at,
            document.updated_at,
            document.source,
            document.external_id,
        ),
    ).fetchone()
    return None if row is None else row[0]


def _existing_chunks(conn, rows):
    result = {}
    by_source = {}
    for row in rows:
        by_source.setdefault(row[0], []).append(row[1])
    for source, identifiers in by_source.items():
        for row in conn.execute(
            """
            SELECT source, id, document_id, field, text, chunk_index,
                   content_hash, metadata
            FROM public.chunks
            WHERE source = %s AND id = ANY(%s)
            """,
            (source, identifiers),
        ):
            result[(row[0], row[1])] = row[2:]
    return result


def _existing_sentences(conn, chunk_rows):
    result = {}
    by_source = {}
    for source, chunk_id, *_ in chunk_rows:
        by_source.setdefault(source, []).append(chunk_id)
    for source, identifiers in by_source.items():
        for row in conn.execute(
            """
            SELECT source, chunk_id, sentence_index, sentence, embedding_model
            FROM public.sentences
            WHERE source = %s AND chunk_id = ANY(%s)
            """,
            (source, identifiers),
        ):
            key = row[:3]
            if key in result:
                raise ValueError("sentence content conflict")
            result[key] = row[3:]
    return result


def write_document_batch(conn, run, records, identities, embeddings):
    """Write one deterministic document batch without accepting changed content."""
    records = tuple(records)
    if not records:
        raise ValueError("document batch must not be empty")
    ensure_permissions(
        conn, (record.document.acl for record in records), identities
    )

    document_rows, new_document_rows = [], []
    for record in records:
        document = record.document
        document_id = stable_uuid(
            "document", f"{document.source}:{document.external_id}"
        )
        document_row = (
            document_id,
            document.source,
            document.kind,
            document.external_id,
            document_id,
            permission_id(document.acl),
            document.title,
            document.body,
            document.author,
            document.url,
            document.container,
            Jsonb(document.raw_payload),
            document.created_at,
            document.updated_at,
        )
        document_rows.append(document_row)
        match = _document_matches(
            conn, document, document_id, permission_id(document.acl)
        )
        if match is False:
            raise ValueError("document content conflict")
        if match is None:
            new_document_rows.append(document_row)

    _executemany(
        conn,
        """
        INSERT INTO public.documents (
            id, source, kind, external_id, root_document_id,
            permission_set_id, title, body, author, url, container,
            raw_payload, source_created_at, source_updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT DO NOTHING
        """,
        new_document_rows,
    )
    if any(
        not _document_matches(
            conn,
            record.document,
            document_row[0],
            document_row[5],
        )
        for record, document_row in zip(records, document_rows, strict=True)
    ):
        raise ValueError("document content conflict")

    chunk_rows = []
    expected_sentences = []
    for record, document_row in zip(records, document_rows, strict=True):
        document = record.document
        document_id = document_row[0]
        for field, texts in document.fields.items():
            for index, text in enumerate(filter(None, texts)):
                chunk_id = f"{document.external_id}:{field}:{index}"
                content_hash = hashlib.sha256(text.encode()).hexdigest()
                chunk_rows.append(
                    (
                        document.source,
                        chunk_id,
                        document_id,
                        field,
                        text,
                        index,
                        content_hash,
                        Jsonb({"external_id": document.external_id}),
                    )
                )
                if field != "issue_metadata":
                    for sentence_index, sentence in enumerate(sentences(text)):
                        try:
                            embedding = embeddings[sentence]
                        except KeyError as error:
                            raise ValueError("missing embedding") from error
                        expected_sentences.append(
                            (
                                document.source,
                                chunk_id,
                                sentence_index,
                                sentence,
                                embedding
                                if isinstance(embedding, str)
                                else encoded_vector(embedding),
                                run.embedding_model,
                            )
                        )

    existing_chunks = _existing_chunks(conn, chunk_rows)
    new_chunk_rows = []
    for row in chunk_rows:
        existing = existing_chunks.get(row[:2])
        expected = (*row[2:7], row[7].obj)
        if existing is not None and existing != expected:
            raise ValueError("chunk content hash conflict")
        if existing is None:
            new_chunk_rows.append(row)
    _executemany(
        conn,
        """
        INSERT INTO public.chunks (
            source, id, document_id, field, text, chunk_index,
            content_hash, metadata
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        new_chunk_rows,
    )
    if _existing_chunks(conn, chunk_rows) != {
        row[:2]: (*row[2:7], row[7].obj) for row in chunk_rows
    }:
        raise ValueError("chunk content hash conflict")

    existing_sentences = _existing_sentences(conn, chunk_rows)
    expected_keys = {row[:3] for row in expected_sentences}
    if set(existing_sentences).difference(expected_keys):
        raise ValueError("sentence content conflict")
    new_sentence_rows = []
    for row in expected_sentences:
        existing = existing_sentences.get(row[:3])
        if existing is not None and existing != (row[3], row[5]):
            raise ValueError("sentence content conflict")
        if existing is None:
            new_sentence_rows.append(row)
    _executemany(
        conn,
        """
        INSERT INTO public.sentences (
            source, chunk_id, sentence_index, sentence, embedding,
            embedding_model
        ) VALUES (%s, %s, %s, %s, %s::halfvec, %s)
        """,
        new_sentence_rows,
    )

    last = records[-1]
    return BatchReport(
        len(new_document_rows),
        len(new_chunk_rows),
        len(new_sentence_rows),
        last.line_number + 1,
        last.next_offset,
        0,
        last.source,
    )
