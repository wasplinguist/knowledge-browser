"""Import the manifest-validated company dataset into PostgreSQL."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import uuid4

from psycopg.types.json import Jsonb

from .embedding_index import sentences


@dataclass(frozen=True, slots=True)
class ImportReport:
    users: int
    documents: int
    chunks: int
    sentences: int


def _acl_key(acl):
    if acl is None:
        acl = {}
    if not isinstance(acl, dict):
        raise ValueError("ACL must be an object")
    serialized = json.dumps(acl, sort_keys=True, separators=(",", ":"))
    return acl, hashlib.sha256(serialized.encode()).hexdigest()


def _import_dataset(conn, dataset, embeddings, *, model):
    user_ids = {}
    for user in dataset.users:
        row = conn.execute(
            """
            INSERT INTO users (email, name, raw_payload)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (user["email"], user["name"], Jsonb(user["raw_payload"])),
        ).fetchone()
        user_ids[user["email"]] = row[0]

    group_ids = {}
    for group in dataset.groups:
        row = conn.execute(
            """
            INSERT INTO groups (name, raw_payload)
            VALUES (%s, %s)
            RETURNING id
            """,
            (group["name"], Jsonb(group["raw_payload"])),
        ).fetchone()
        group_ids[group["name"]] = row[0]
        for email in group["members"]:
            try:
                user_id = user_ids[email]
            except KeyError as error:
                raise ValueError(f"group has unknown user: {email}") from error
            conn.execute(
                "INSERT INTO group_memberships (group_id, user_id) VALUES (%s, %s)",
                (row[0], user_id),
            )

    permission_ids = {}
    document_permissions = []
    for document in dataset.documents:
        acl, key = _acl_key(document.acl)
        permission_id = permission_ids.get(key)
        if permission_id is None:
            permission_id = uuid4()
            permission_ids[key] = permission_id
            conn.execute(
                """
                INSERT INTO permission_sets (id, visibility, raw_payload)
                VALUES (%s, %s, %s)
                """,
                (
                    permission_id,
                    "company" if acl.get("company") else "restricted",
                    Jsonb({"key": key, **acl}),
                ),
            )
            for email in acl.get("users", []):
                try:
                    user_id = user_ids[email]
                except KeyError as error:
                    raise ValueError(f"ACL has unknown user: {email}") from error
                conn.execute(
                    """
                    INSERT INTO permission_set_users (permission_set_id, user_id)
                    VALUES (%s, %s)
                    """,
                    (permission_id, user_id),
                )
            for name in acl.get("groups", []):
                try:
                    group_id = group_ids[name]
                except KeyError as error:
                    raise ValueError(f"ACL has unknown group: {name}") from error
                conn.execute(
                    """
                    INSERT INTO permission_set_groups (permission_set_id, group_id)
                    VALUES (%s, %s)
                    """,
                    (permission_id, group_id),
                )
        document_permissions.append(permission_id)

    document_count = chunk_count = sentence_count = 0
    encoded_embeddings = {}
    for document, permission_id in zip(
        dataset.documents, document_permissions, strict=True
    ):
        document_id = uuid4()
        conn.execute(
            """
            INSERT INTO documents (
                id, source, kind, external_id, root_document_id,
                permission_set_id, title, body, author, url, container,
                raw_payload, source_created_at, source_updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                document_id,
                document.source,
                document.kind,
                document.external_id,
                document_id,
                permission_id,
                document.title,
                document.body,
                document.author,
                document.url,
                document.container,
                Jsonb(document.raw_payload),
                document.created_at,
                document.updated_at,
            ),
        )
        document_count += 1
        for field, texts in document.fields.items():
            for index, text in enumerate(filter(None, texts)):
                chunk_id = f"{document.external_id}:{field}:{index}"
                conn.execute(
                    """
                    INSERT INTO chunks (
                        source, id, document_id, field, text, chunk_index,
                        content_hash, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        document.source,
                        chunk_id,
                        document_id,
                        field,
                        text,
                        index,
                        hashlib.sha256(text.encode()).hexdigest(),
                        Jsonb({"external_id": document.external_id}),
                    ),
                )
                chunk_count += 1
                if field == "issue_metadata":
                    continue
                for sentence_index, sentence in enumerate(sentences(text)):
                    try:
                        embedding = embeddings[sentence]
                    except KeyError as error:
                        raise ValueError("missing embedding") from error
                    encoded = encoded_embeddings.get(id(embedding))
                    if encoded is None:
                        encoded = "[" + ",".join(map(str, embedding)) + "]"
                        encoded_embeddings[id(embedding)] = encoded
                    conn.execute(
                        """
                        INSERT INTO sentences (
                            source, chunk_id, sentence_index, sentence,
                            embedding, embedding_model
                        ) VALUES (%s, %s, %s, %s, %s::halfvec, %s)
                        """,
                        (
                            document.source,
                            chunk_id,
                            sentence_index,
                            sentence,
                            encoded,
                            model,
                        ),
                    )
                    sentence_count += 1
    return ImportReport(len(user_ids), document_count, chunk_count, sentence_count)


def import_dataset(conn, dataset, embeddings, *, model):
    """Atomically import all dataset records and their sentence embeddings."""
    with conn.transaction():
        return _import_dataset(conn, dataset, embeddings, model=model)
