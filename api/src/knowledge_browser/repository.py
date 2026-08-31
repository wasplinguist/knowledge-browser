from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from .db import allowed_document_sql


@dataclass(frozen=True, slots=True)
class Identity:
    id: UUID
    email: str
    name: str


@dataclass(frozen=True, slots=True)
class Document:
    id: UUID
    source: str
    kind: str
    external_id: str
    parent_document_id: UUID | None
    root_document_id: UUID
    title: str
    body: str
    author: str | None
    url: str | None
    container: str | None
    raw_payload: dict[str, Any]
    source_created_at: datetime | None
    source_updated_at: datetime | None
    indexed_at: datetime


@dataclass(frozen=True, slots=True)
class Chunk:
    source: str
    id: str
    document_id: UUID
    field: str
    text: str
    chunk_index: int
    content_hash: str
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Sentence:
    source: str
    id: int
    chunk_id: str
    sentence_index: int
    sentence: str
    embedding: str
    embedding_model: str


def resolve_identity(conn, email: str) -> Identity | None:
    row = conn.execute(
        """
        SELECT id, email, name
        FROM users
        WHERE email = %(identity)s OR id::text = lower(%(identity)s)
        """,
        {"identity": email},
    ).fetchone()
    return Identity(*row) if row else None


def get_document(
    conn, user_id: UUID | str, source: str, external_id: str
) -> Document | None:
    row = conn.execute(
        f"""
        SELECT documents.id, documents.source, documents.kind,
               documents.external_id, documents.parent_document_id,
               documents.root_document_id, documents.title, documents.body,
               documents.author, documents.url, documents.container,
               documents.raw_payload, documents.source_created_at,
               documents.source_updated_at, documents.indexed_at
        FROM documents
        JOIN documents root ON root.id = documents.root_document_id
        WHERE {allowed_document_sql()}
          AND {allowed_document_sql(document_alias="root")}
          AND documents.source = %(source)s
          AND documents.external_id = %(external_id)s
        """,
        {"user_id": user_id, "source": source, "external_id": external_id},
    ).fetchone()
    return Document(*row) if row else None


def get_document_chunks(
    conn, user_id: UUID | str, source: str, external_id: str
) -> list[Chunk]:
    rows = conn.execute(
        f"""
        SELECT chunks.source, chunks.id, chunks.document_id, chunks.field,
               chunks.text, chunks.chunk_index, chunks.content_hash, chunks.metadata
        FROM chunks
        JOIN documents ON documents.id = chunks.document_id
        JOIN documents root ON root.id = documents.root_document_id
        WHERE {allowed_document_sql()}
          AND {allowed_document_sql(document_alias="root")}
          AND documents.source = %(source)s
          AND documents.external_id = %(external_id)s
        ORDER BY chunks.chunk_index, chunks.id
        """,
        {"user_id": user_id, "source": source, "external_id": external_id},
    ).fetchall()
    return [Chunk(*row) for row in rows]


def get_chunk_sentences(
    conn, user_id: UUID | str, source: str, chunk_id: str
) -> list[Sentence]:
    rows = conn.execute(
        f"""
        SELECT sentences.source, sentences.id, sentences.chunk_id,
               sentences.sentence_index, sentences.sentence,
               sentences.embedding::text, sentences.embedding_model
        FROM sentences
        JOIN chunks
          ON chunks.source = sentences.source AND chunks.id = sentences.chunk_id
        JOIN documents ON documents.id = chunks.document_id
        JOIN documents root ON root.id = documents.root_document_id
        WHERE {allowed_document_sql()}
          AND {allowed_document_sql(document_alias="root")}
          AND sentences.source = %(source)s
          AND sentences.chunk_id = %(chunk_id)s
        ORDER BY sentences.sentence_index, sentences.id
        """,
        {"user_id": user_id, "source": source, "chunk_id": chunk_id},
    ).fetchall()
    return [Sentence(*row) for row in rows]
