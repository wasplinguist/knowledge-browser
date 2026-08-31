from collections.abc import Iterable
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb


def record_search(
    conn,
    *,
    user_id: UUID | str,
    session_id: UUID | None,
    query: str,
    normalized_query: str,
    source: str | None,
    profile: str,
    results: Iterable[dict[str, Any]],
    embedding_available: bool,
    duration_ms: int,
) -> UUID:
    results = list(results)
    result_ids = [
        {"source": result["source"], "external_id": result["external_id"]}
        for result in results[:10]
    ]
    return conn.execute(
        """
        INSERT INTO search_events (
          user_id, session_id, query, normalized_query, source, profile,
          result_ids, result_count, embedding_available, duration_ms
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            user_id,
            session_id,
            query,
            normalized_query,
            source,
            profile,
            Jsonb(result_ids),
            len(results),
            embedding_available,
            duration_ms,
        ),
    ).fetchone()[0]


def record_click(
    conn,
    *,
    search_id: UUID,
    user_id: UUID | str,
    source: str,
    external_id: str,
    rank: int,
) -> bool:
    displayed = conn.execute(
        """
        SELECT 1
        FROM search_events
        WHERE id = %s
          AND user_id = %s
          AND %s > 0
          AND result_ids -> (%s - 1) = jsonb_build_object(
                'source', %s::text, 'external_id', %s::text
              )
        """,
        (search_id, user_id, rank, rank, source, external_id),
    ).fetchone()
    if not displayed:
        return False
    conn.execute(
        """
        INSERT INTO search_clicks (search_id, user_id, source, external_id, rank)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (search_id, source, external_id) DO NOTHING
        """,
        (search_id, user_id, source, external_id, rank),
    )
    return True
