import os
from urllib.parse import quote


def database_url() -> str:
    if url := os.environ.get("DATABASE_URL"):
        return url

    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    database = quote(os.environ.get("POSTGRES_DB", "knowledge_search"), safe="")
    user = quote(os.environ.get("POSTGRES_USER", "postgres"), safe="")
    password = quote(os.environ.get("POSTGRES_PASSWORD", "postgres"), safe="")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"
