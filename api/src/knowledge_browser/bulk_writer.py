"""Deterministic batched writes for bulk-import identities and ACLs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
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
    cache_hits: int = 0
    cache_misses: int = 0
    concurrency: int = 0
    retries: int = 0
    sentences_per_second: float = 0.0
    estimated_remaining_seconds: float = 0.0


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


def _timestamp(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _existing_documents(conn, rows):
    result = {}
    by_source = {}
    for row in rows:
        by_source.setdefault(row[1], []).append(row[3])
    for source, external_ids in by_source.items():
        for row in conn.execute(
            """
            SELECT source, external_id, id, kind, parent_document_id,
                   root_document_id, permission_set_id, title, body, author,
                   url, container, raw_payload, source_created_at,
                   source_updated_at
            FROM public.documents
            WHERE source = %s AND external_id = ANY(%s)
            """,
            (source, external_ids),
        ):
            result[row[:2]] = row[2:]
    return result


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

    document_rows = []
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

    existing_documents = _existing_documents(conn, document_rows)
    new_document_rows = []
    for row in document_rows:
        existing = existing_documents.get((row[1], row[3]))
        expected = (
            row[0],
            row[2],
            None,
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
            row[10],
            row[11].obj,
            _timestamp(row[12]),
            _timestamp(row[13]),
        )
        if existing is not None and existing != expected:
            raise ValueError("document content conflict")
        if existing is None:
            new_document_rows.append(row)

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
