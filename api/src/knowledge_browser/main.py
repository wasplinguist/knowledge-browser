from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
import time
from uuid import UUID

from fastapi import FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .analytics import record_click, record_search
from .db import connection
from .profiles import SearchProfile, expand_query, load_profile
from .repository import resolve_identity
from .search import hybrid_search


SOURCES = ("confluence", "github", "jira", "slack")


class ClickRequest(BaseModel):
    source: str
    external_id: str
    rank: int


def _error(code: str, message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def create_app(
    connection_factory: Callable[[], AbstractContextManager] = connection,
    embed: Callable[[str], list[float] | None] | None = None,
    profile: SearchProfile | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Knowledge Browser",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    search_profile = profile or load_profile(
        Path(__file__).parents[3] / "search" / "profiles" / "released.json"
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError):
        return _error("invalid_request", "invalid request")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/demo-users")
    def demo_users():
        try:
            with connection_factory() as conn:
                rows = conn.execute(
                    "SELECT id, email, name FROM users ORDER BY name"
                ).fetchall()
            return {
                "items": [
                    {"id": str(row[0]), "email": row[1], "name": row[2]}
                    for row in rows
                ]
            }
        except Exception:
            return _error("database_unavailable", "database is unavailable", 503)

    @app.get("/api/search")
    def search_route(
        q: str = "",
        source: str | None = None,
        x_demo_user_id: str | None = Header(default=None),
        x_search_session_id: str | None = Header(default=None),
    ):
        if not x_demo_user_id:
            return _error("missing_demo_user", "X-Demo-User-Id is required")
        if not q.strip():
            return _error("invalid_query", "query must not be empty")
        if source and source not in SOURCES:
            return _error("invalid_source", "source is invalid")
        try:
            with connection_factory() as conn:
                identity = resolve_identity(conn, x_demo_user_id)
                if identity is None:
                    return _error("unknown_demo_user", "select a valid demo user")
                normalized_query = expand_query(q.strip(), search_profile)
                started_at = time.perf_counter()
                try:
                    embedding = embed(normalized_query) if embed else None
                except Exception:
                    embedding = None
                items = hybrid_search(
                    conn,
                    identity.id,
                    q.strip(),
                    embedding,
                    source,
                    search_profile,
                )
                duration_ms = max(
                    1, int((time.perf_counter() - started_at) * 1000)
                )
                try:
                    session_id = UUID(x_search_session_id or "")
                except (TypeError, ValueError):
                    session_id = None
                try:
                    with conn.transaction():
                        search_id = record_search(
                            conn,
                            user_id=identity.id,
                            session_id=session_id,
                            query=q,
                            normalized_query=normalized_query,
                            source=source,
                            profile=search_profile.name,
                            results=items,
                            embedding_available=embedding is not None,
                            duration_ms=duration_ms,
                        )
                except Exception:
                    search_id = None
        except Exception:
            return _error("search_unavailable", "search is unavailable", 503)

        facets = {
            item_source: sum(item["source"] == item_source for item in items)
            for item_source in SOURCES
        }
        return {
            "search_id": str(search_id) if search_id else None,
            "profile": search_profile.name,
            "items": items,
            "facets": facets,
        }

    @app.post("/api/search-events/{search_id}/click")
    def click_route(
        search_id: UUID,
        body: ClickRequest,
        x_demo_user_id: str | None = Header(default=None),
    ):
        if not x_demo_user_id:
            return _error("missing_demo_user", "X-Demo-User-Id is required")
        if body.source not in SOURCES or body.rank < 1:
            return _error("invalid_request", "invalid request")
        try:
            with connection_factory() as conn:
                identity = resolve_identity(conn, x_demo_user_id)
                if identity is None:
                    return _error("unknown_demo_user", "select a valid demo user")
                if not record_click(
                    conn,
                    search_id=search_id,
                    user_id=identity.id,
                    source=body.source,
                    external_id=body.external_id,
                    rank=body.rank,
                ):
                    return _error(
                        "search_event_not_found", "search event is unavailable", 404
                    )
            return Response(status_code=204)
        except Exception:
            return _error(
                "search_event_unavailable", "search event is unavailable", 503
            )

    return app


app = create_app()
