from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(
        title="Knowledge Browser",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
