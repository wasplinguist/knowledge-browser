from typing import Any
from uuid import UUID

from .db import allowed_document_sql
from .profiles import SearchProfile, expand_query


SNIPPET_LENGTH = 280


def _source_filter(source: str | None) -> tuple[str, dict[str, str]]:
    if source is None:
        return "", {}
    return "AND chunks.source = %(source)s", {"source": source}


def _result(row: tuple[Any, ...]) -> dict[str, Any]:
    text = row[2]
    return {
        "chunk_id": row[0],
        "field": row[1],
        "matched_field": row[1],
        "excerpt": (
            text
            if len(text) <= SNIPPET_LENGTH
            else text[:SNIPPET_LENGTH].rstrip() + "…"
        ),
        "root_id": row[3],
        "external_id": row[4],
        "title": row[5],
        "source": row[6],
        "author": row[7],
        "matched_author": row[8],
        "container": row[9],
        "updated_at": row[10],
        "url": row[11],
        "is_child": row[12],
        "created_at": row[13],
        "chunk_index": row[14],
        "matched_external_id": row[15],
        "matched_created_at": row[16],
        "matched_updated_at": row[17],
    }


def keyword_search(
    conn,
    user_id: UUID | str,
    query: str,
    source: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    source_sql, source_parameters = _source_filter(source)
    rows = conn.execute(
        f"""
        SELECT chunks.id, chunks.field, chunks.text, root.id, root.external_id,
               root.title, root.source, root.author, documents.author,
               root.container, root.source_updated_at, root.url,
               documents.id <> root.id, root.source_created_at,
               chunks.chunk_index, documents.external_id,
               documents.source_created_at, documents.source_updated_at
        FROM chunks
        JOIN documents ON documents.id = chunks.document_id
        JOIN documents root ON root.id = documents.root_document_id
        WHERE root.root_document_id = root.id
          AND {allowed_document_sql()}
          AND {allowed_document_sql(document_alias="root")}
          AND chunks.fts @@ websearch_to_tsquery('english', %(query)s)
          {source_sql}
        ORDER BY ts_rank_cd(
                   chunks.fts, websearch_to_tsquery('english', %(query)s)
                 ) DESC,
                 chunks.source, chunks.id
        LIMIT %(limit)s
        """,
        {
            "user_id": user_id,
            "query": query,
            "limit": max(1, min(limit, 100)),
            **source_parameters,
        },
    ).fetchall()
    return [_result(row) for row in rows]


def semantic_search(
    conn,
    user_id: UUID | str,
    query_embedding: list[float] | None,
    source: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not query_embedding:
        return []
    source_sql, source_parameters = _source_filter(source)
    embedding = "[" + ",".join(map(str, query_embedding)) + "]"
    rows = conn.execute(
        f"""
        WITH best_sentences AS (
          SELECT DISTINCT ON (chunks.source, chunks.id)
                 chunks.id, chunks.field, chunks.text, root.id AS root_id,
                 root.external_id, root.title, root.source, root.author,
                 documents.author AS matched_author, root.container,
                 root.source_updated_at, root.url,
                 documents.id <> root.id AS is_child,
                 root.source_created_at, chunks.chunk_index,
                 documents.external_id AS matched_external_id,
                 documents.source_created_at AS matched_created_at,
                 documents.source_updated_at AS matched_updated_at,
                 sentences.embedding <=> %(embedding)s::halfvec AS distance
          FROM sentences
          JOIN chunks
            ON chunks.source = sentences.source AND chunks.id = sentences.chunk_id
          JOIN documents ON documents.id = chunks.document_id
          JOIN documents root ON root.id = documents.root_document_id
          WHERE root.root_document_id = root.id
            AND {allowed_document_sql()}
            AND {allowed_document_sql(document_alias="root")}
            {source_sql}
          ORDER BY chunks.source, chunks.id,
                   sentences.embedding <=> %(embedding)s::halfvec,
                   sentences.id
        )
        SELECT id, field, text, root_id, external_id, title, source, author,
               matched_author, container, source_updated_at, url, is_child,
               source_created_at, chunk_index, matched_external_id,
               matched_created_at, matched_updated_at
        FROM best_sentences
        ORDER BY distance, id
        LIMIT %(limit)s
        """,
        {
            "user_id": user_id,
            "embedding": embedding,
            "limit": max(1, min(limit, 100)),
            **source_parameters,
        },
    ).fetchall()
    return [_result(row) for row in rows]


def hybrid_search(
    conn,
    user_id: UUID | str,
    query: str,
    query_embedding: list[float] | None,
    source: str | None = None,
    profile: SearchProfile | None = None,
) -> list[dict[str, Any]]:
    profile = profile or SearchProfile(name="default")
    expanded_query = expand_query(query, profile)
    channels = (
        (
            keyword_search(
                conn, user_id, expanded_query, source, profile.keyword_limit
            )
            if profile.keyword_weight
            else [],
            profile.keyword_weight,
        ),
        (
            semantic_search(
                conn, user_id, query_embedding, source, profile.semantic_limit
            )
            if profile.semantic_weight
            else [],
            profile.semantic_weight,
        ),
    )
    grouped: dict[Any, dict[str, Any]] = {}
    for matches, weight in channels:
        for rank, match in enumerate(matches, start=1):
            result = grouped.setdefault(
                match["root_id"],
                {**match, "score": 0.0, "best_rank": rank},
            )
            result["score"] += weight / (profile.rrf_k + rank)
            if (match["is_child"] and not result["is_child"]) or (
                match["is_child"] == result["is_child"]
                and rank < result["best_rank"]
            ):
                score = result["score"]
                result.update(match)
                result["score"] = score
                result["best_rank"] = rank

    items = sorted(
        grouped.values(), key=lambda item: (-item["score"], item["external_id"])
    )
    for item in items:
        item.pop("root_id", None)
        item.pop("best_rank", None)
        item.pop("is_child", None)
    return items

