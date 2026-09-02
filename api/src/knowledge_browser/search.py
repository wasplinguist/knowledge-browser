from datetime import datetime, timezone
import re
from typing import Any
from uuid import UUID

from .db import allowed_document_sql
from .profiles import SearchProfile, expand_query


SNIPPET_LENGTH = 280
SEMANTIC_CANDIDATE_MULTIPLIER = 20
SENTENCE_TABLES = {
    source: f"{source}_sentences"
    for source in ("jira", "confluence", "slack", "github")
}
FRESHNESS_QUERY = re.compile(
    r"\b(current|latest|recent|newest|now|today|up[- ]to[- ]date)\b",
    re.IGNORECASE,
)
JIRA_KEY = re.compile(
    r"(?<![A-Z0-9])([A-Z][A-Z0-9]{1,15}-\d+)(?![A-Z0-9])",
    re.IGNORECASE,
)
AUTHORITY_SIGNALS = (
    ("github", re.compile(
        r"\b(pull request|pr|commit|merged|code|repository)\b", re.IGNORECASE,
    )),
    ("confluence", re.compile(
        r"\b(policy|design|decision|runbook|documentation|document|plan)\b",
        re.IGNORECASE,
    )),
    ("slack", re.compile(
        r"\b(first mentioned|message|thread|conversation|said)\b", re.IGNORECASE,
    )),
    ("jira", re.compile(
        r"\b(status|assignee|ticket|issue|target version|fix version|affected version)\b",
        re.IGNORECASE,
    )),
)
EXPLICIT_SOURCES = {
    source: re.compile(rf"\b{source}\b", re.IGNORECASE)
    for source in ("jira", "github", "confluence", "slack")
}


def _timestamp(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _evidence_timestamp(item: dict[str, Any]) -> datetime:
    return max(
        _timestamp(item.get("updated_at")),
        _timestamp(item.get("matched_updated_at")),
    )


def _authoritative_source(query: str) -> str | None:
    explicit = [
        source for source, pattern in EXPLICIT_SOURCES.items()
        if pattern.search(query)
    ]
    if len(explicit) == 1:
        return explicit[0]
    if len(explicit) > 1:
        return None
    return next(
        (source for source, pattern in AUTHORITY_SIGNALS if pattern.search(query)),
        None,
    )


def _primary_project_roots(conn, user_id: UUID | str, root_ids: list[Any]) -> set[Any]:
    if not root_ids:
        return set()
    return {
        row[0]
        for row in conn.execute(
            """
            SELECT documents.id
            FROM documents
            JOIN users ON users.id = %(user_id)s
            WHERE documents.id = ANY(%(root_ids)s)
              AND documents.raw_payload->'project_ids' ? COALESCE(
                    users.raw_payload->'raw_payload'->>'primary_project_id',
                    users.raw_payload->>'primary_project_id'
                  )
            """,
            {"user_id": user_id, "root_ids": root_ids},
        ).fetchall()
    }


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
    sentence_table = SENTENCE_TABLES.get(source, "sentences")
    source_sql = (
        "AND sentences.source = %(source)s"
        if source is not None and source not in SENTENCE_TABLES
        else ""
    )
    source_parameters = {"source": source} if source_sql else {}
    embedding = "[" + ",".join(map(str, query_embedding)) + "]"
    bounded_limit = max(1, min(limit, 100))
    # Keep denied rows inside the ANN filter. OFFSET 0 below prevents PostgreSQL
    # from pulling that ACL check above the partition HNSW scan.
    conn.execute("SET LOCAL hnsw.iterative_scan = 'strict_order'")
    rows = conn.execute(
        f"""
        WITH nearest_sentences AS MATERIALIZED (
          SELECT sentences.source AS chunk_source,
                 sentences.chunk_id,
                 sentences.id AS sentence_id,
                 sentences.embedding <=> %(embedding)s::halfvec AS distance
          FROM {sentence_table} AS sentences
          WHERE sentences.embedding IS NOT NULL
            {source_sql}
            AND EXISTS (
              SELECT 1
              FROM chunks
              JOIN documents ON documents.id = chunks.document_id
              JOIN documents root ON root.id = documents.root_document_id
              WHERE chunks.source = sentences.source
                AND chunks.id = sentences.chunk_id
                AND root.root_document_id = root.id
                AND {allowed_document_sql()}
                AND {allowed_document_sql(document_alias="root")}
              OFFSET 0
            )
          ORDER BY sentences.embedding <=> %(embedding)s::halfvec
          LIMIT %(candidate_limit)s
        ),
        best_sentences AS (
          SELECT DISTINCT ON (chunks.source, chunks.id)
                 chunks.source AS chunk_source, chunks.id, chunks.field,
                 chunks.text, root.id AS root_id,
                 root.external_id, root.title, root.source, root.author,
                 documents.author AS matched_author, root.container,
                 root.source_updated_at, root.url,
                 documents.id <> root.id AS is_child,
                 root.source_created_at, chunks.chunk_index,
                 documents.external_id AS matched_external_id,
                 documents.source_created_at AS matched_created_at,
                 documents.source_updated_at AS matched_updated_at,
                 nearest_sentences.distance
          FROM nearest_sentences
          JOIN chunks
            ON chunks.source = nearest_sentences.chunk_source
           AND chunks.id = nearest_sentences.chunk_id
          JOIN documents ON documents.id = chunks.document_id
          JOIN documents root ON root.id = documents.root_document_id
          ORDER BY chunks.source, chunks.id, nearest_sentences.distance,
                   nearest_sentences.sentence_id
        )
        SELECT id, field, text, root_id, external_id, title, source, author,
               matched_author, container, source_updated_at, url, is_child,
               source_created_at, chunk_index, matched_external_id,
               matched_created_at, matched_updated_at
        FROM best_sentences
        ORDER BY distance, chunk_source, id
        LIMIT %(limit)s
        """,
        {
            "user_id": user_id,
            "embedding": embedding,
            "candidate_limit": bounded_limit * SEMANTIC_CANDIDATE_MULTIPLIER,
            "limit": bounded_limit,
            **source_parameters,
        },
    ).fetchall()
    return [_result(row) for row in rows]


def read_chunk(
    conn,
    user_id: UUID | str,
    source: str,
    chunk_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
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
          AND chunks.source = %(source)s
          AND chunks.id = %(chunk_id)s
        """,
        {"user_id": user_id, "source": source, "chunk_id": chunk_id},
    ).fetchone()
    if row is None:
        return None
    result = _result(row)
    result["text"] = row[2]
    return result


def read_chunk_context(
    conn,
    user_id: UUID | str,
    source: str,
    chunk_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    if limit < 1:
        return []
    rows = conn.execute(
        f"""
        SELECT context_chunks.id, context_chunks.field, context_chunks.text,
               root.id, root.external_id, root.title, root.source, root.author,
               documents.author, root.container, root.source_updated_at,
               root.url, documents.id <> root.id, root.source_created_at,
               context_chunks.chunk_index, documents.external_id,
               documents.source_created_at, documents.source_updated_at
        FROM chunks selected_chunk
        JOIN documents ON documents.id = selected_chunk.document_id
        JOIN documents root ON root.id = documents.root_document_id
        JOIN chunks context_chunks
          ON context_chunks.document_id = documents.id
         AND context_chunks.source = selected_chunk.source
         AND context_chunks.field = selected_chunk.field
        WHERE root.root_document_id = root.id
          AND {allowed_document_sql()}
          AND {allowed_document_sql(document_alias="root")}
          AND selected_chunk.source = %(source)s
          AND selected_chunk.id = %(chunk_id)s
        ORDER BY context_chunks.id = selected_chunk.id DESC,
                 context_chunks.chunk_index, context_chunks.id
        LIMIT %(limit)s
        """,
        {
            "user_id": user_id,
            "source": source,
            "chunk_id": chunk_id,
            "limit": min(limit, 100),
        },
    ).fetchall()
    context = []
    for row in rows:
        result = _result(row)
        result["text"] = row[2]
        context.append(result)
    return context


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
                {
                    **match,
                    "score": 0.0,
                    "best_rank": rank,
                    "evidence_timestamp": _evidence_timestamp(match),
                    "jira_keys": set(),
                },
            )
            result["score"] += weight / (profile.rrf_k + rank)
            result["evidence_timestamp"] = max(
                result["evidence_timestamp"], _evidence_timestamp(match)
            )
            if (
                match["source"] == "jira"
                and match["matched_field"] == "issue_metadata"
            ):
                result["jira_keys"].update(
                    found.group(1).casefold()
                    for found in JIRA_KEY.finditer(match["excerpt"])
                )
            if (match["is_child"] and not result["is_child"]) or (
                match["is_child"] == result["is_child"]
                and rank < result["best_rank"]
            ):
                score = result["score"]
                result.update(match)
                result["score"] = score
                result["best_rank"] = rank

    if profile.freshness_weight and FRESHNESS_QUERY.search(query):
        recent = sorted(
            grouped.values(),
            key=lambda item: item["evidence_timestamp"],
            reverse=True,
        )
        if recent:
            recent[0]["score"] += profile.freshness_weight / (profile.rrf_k + 1)

    authoritative_source = (
        _authoritative_source(query) if profile.authority_weight else None
    )
    if authoritative_source:
        authoritative = sorted(
            (
                item for item in grouped.values()
                if item["source"] == authoritative_source
            ),
            key=lambda item: (-item["score"], item["external_id"]),
        )
        for rank, item in enumerate(authoritative, start=1):
            item["score"] += profile.authority_weight / (profile.rrf_k + rank)

    jira_key = JIRA_KEY.search(query) if profile.jira_key_weight else None
    if jira_key:
        exact_key = jira_key.group(1).casefold()
        exact_tickets = [
            item for item in grouped.values()
            if exact_key in item["jira_keys"]
        ]
        for rank, item in enumerate(exact_tickets, start=1):
            item["score"] += profile.jira_key_weight / (profile.rrf_k + rank)

    if profile.personalization_weight:
        personal_roots = _primary_project_roots(conn, user_id, list(grouped))
        personal = sorted(
            (
                item for root_id, item in grouped.items()
                if root_id in personal_roots
            ),
            key=lambda item: (-item["score"], item["external_id"]),
        )
        for rank, item in enumerate(personal, start=1):
            item["score"] += profile.personalization_weight / (profile.rrf_k + rank)

    items = sorted(
        grouped.values(), key=lambda item: (-item["score"], item["external_id"])
    )
    for item in items:
        item.pop("root_id", None)
        item.pop("best_rank", None)
        item.pop("is_child", None)
        item.pop("evidence_timestamp", None)
        item.pop("jira_keys", None)
    return items
