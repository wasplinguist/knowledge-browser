# Knowledge Browser

Knowledge Browser is an ACL-safe company knowledge search product being rebuilt
through approved feature contracts.

## Current scope

The product can read the existing PostgreSQL 17/pgvector database through
ACL-safe repository interfaces and can verify that database before retrieval is
enabled. Search, RAG, and UI behavior arrive in later contract-driven PRs.

## Requirements

- Python 3.12
- Node.js 22.22.2 or newer
- Docker with Compose

## API

```bash
python3.12 -m venv api/.venv
api/.venv/bin/python -m pip install -e './api[dev]'
api/.venv/bin/uvicorn knowledge_browser.main:app --reload --app-dir api/src
```

Open `http://127.0.0.1:8000/api/health`.

## Web

```bash
cd web
npm ci
npm run dev
```

Open the Vite URL printed in the terminal.

## PostgreSQL

By default, the API connects to the existing local `knowledge_search` database
as `postgres` on `localhost:5432`. `DATABASE_URL` takes precedence. Otherwise,
set any of `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, and
`POSTGRES_PASSWORD`.

```bash
docker compose up -d db
docker compose ps
```

Use a different `POSTGRES_PORT` and a dedicated database whose name contains
`_test` for each parallel worktree. Tests default to
`knowledge_browser_compat_test` and never write to `knowledge_search`.

Check an existing populated database without migrating or ingesting data:

```bash
DATABASE_URL='postgresql://postgres:postgres@localhost:5432/knowledge_search' \
  api/.venv/bin/python -m knowledge_browser.db_compat
```

The command is read-only and prints compatibility status plus aggregate counts;
it does not print credentials or company content.

## Verification

```bash
api/.venv/bin/python -m pytest -q -m "unit or integration" api/tests
(cd web && npm test -- --run)
(cd web && npm run build)
docker compose config --quiet
git diff --check
```

## Product and contribution rules

- Product guardrails: `docs/PRODUCT_INTENT.md`
- Feature contract template: `docs/contracts/FEATURE_CONTRACT_TEMPLATE.md`
- Migration design: `docs/superpowers/specs/2026-08-31-clean-product-migration-design.md`
