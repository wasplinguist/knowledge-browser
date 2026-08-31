# Feature contract: runtime foundation

## Status

Approved

## User outcome

A developer can clone the repository, run a health-only API, open a minimal web
application, and start PostgreSQL with pgvector. Pull requests automatically
check these foundations.

## Evidence

Every later feature needs a reproducible Python, Node.js, browser, database, and
CI base. The new repository currently contains contracts only.

## Scope

- Installable Python package with a FastAPI application.
- `GET /api/health` returning HTTP 200 and `{"status": "ok"}`.
- Minimal React page showing `Knowledge Browser` and `Foundation ready`.
- PostgreSQL 17 with pgvector through Docker Compose.
- CI for API tests, web tests, web build, and Compose validation.
- README commands matching CI.

## Non-goals

- Database tables or migrations.
- Identity, ACL, ingestion, search, RAG, synthetic data, or evaluation.
- Production deployment, authentication, or real connectors.
- UI design beyond a readable shell.

## Dependencies

Only bootstrap commit `89daa76` on `main`.

## Interface and data contract

`GET /api/health` has no request body, query parameters, authentication, or
database dependency. Its JSON response is exactly `{"status": "ok"}`.

The web shell has no API request. Vite proxies future `/api` development calls
to `http://127.0.0.1:8000`.

Docker Compose exposes PostgreSQL on `${POSTGRES_PORT:-5432}` with database
`${POSTGRES_DB:-knowledge_browser}` and local demo credentials. No schema is
mounted in this feature.

## Safety invariants

- No secret is committed.
- The health response contains no environment or database details.
- No external network call occurs during tests.
- No endpoint reads or returns company content.

## Quality and performance

The health test must finish without PostgreSQL. The complete API unit test,
web test, and web build should each finish in under one minute on a normal
development machine.

## Acceptance criteria

- `GET /api/health` returns HTTP 200 and exactly `{"status": "ok"}`.
- The web shell renders both required text labels.
- `docker compose config --quiet` succeeds.
- API tests, web tests, and web production build succeed in CI.
- The repository contains no search, RAG, data, schema, or ACL implementation.

## Verification

- `api/.venv/bin/python -m pytest -q api/tests`
- `npm test -- --run` from `web/`
- `npm run build` from `web/`
- `docker compose config --quiet`
- `git diff --check`

## Source reference

Only dependency choices and general directory names were inspected at final
old-repository commit `8782676`. No old product code or Git history is imported.
