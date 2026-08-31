# Knowledge Browser

Knowledge Browser is an ACL-safe company knowledge search product being rebuilt
through approved feature contracts.

## Current scope

The product can read and verify the existing PostgreSQL 17/pgvector database.
It also supports ACL-safe keyword and semantic retrieval, alias expansion,
reciprocal rank fusion, one result per root document, and query-aware enterprise
ranking for freshness, source authority, exact Jira keys, and primary-project
context. The API exposes
demo-user selection, search, source facets, and safe click analytics. The API
also supports bounded grounded answers when `create_app` receives an
OpenAI-compatible Responses client. Answer citations are limited to ACL-safe
chunks opened during that request. Tests use fake clients and make no paid
calls. The web app provides search, grounded answers, deduplicated provenance,
source facets, and ACL-safe local panels for Jira, Confluence, Slack, and
GitHub data.

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

For local demo search, first read `/api/demo-users`, then send one returned ID
as `X-Demo-User-Id` to `/api/search?q=...`. This header is demo identity only;
it is not real login.

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

The released retrieval settings are stored in
`search/profiles/released.json`. Retrieval accepts a query embedding from its
caller; provider calls are added with the API feature.

## Verification

```bash
# Fast edit loop
api/.venv/bin/python -m pytest -q -m unit api/tests

# Normal API pull-request checks
api/.venv/bin/python -m pytest -q -m "unit or integration" api/tests

# Search and grounded-RAG evaluation, without nightly ACL work
api/.venv/bin/python -m pytest -q \
  -m "(search_eval or rag_eval) and not nightly" api/tests

# Marker safety check
api/.venv/bin/python -m pytest --collect-only -q --strict-markers api/tests

(cd web && npm test -- --run)
(cd web && npm run build)
docker compose config --quiet
git diff --check
```

The exhaustive ACL group is intentionally separate from normal work:

```bash
# Manual/nightly ACL gate. Any root or child leak fails.
NATIVE_EVAL_DATABASE_URL='postgresql://.../knowledge_search' \
  api/.venv/bin/python -m pytest -q -m full_acl api/tests

# Complete release/nightly API suite.
api/.venv/bin/python -m pytest -q api/tests
```

Do not remove search or RAG evaluation to make CI faster. Pull-request CI runs
fast API checks and the small non-nightly evaluation. Nightly/manual CI keeps
the full ACL and complete-suite gates. Evaluation reports are CI artifacts and
are not committed to the repository.

The native ACL job uses the committed 603-query text projection and every user
in the configured read-only database. This is 60,300 query/user pairs for the
current 100-user corpus. The job skips when its protected database secret is
not configured; a skipped job is not release proof. Never point it at a write
database. It starts a read-only transaction and requires zero root and child
leaks.

## Product and contribution rules

- Product guardrails: `docs/PRODUCT_INTENT.md`
- Feature contract template: `docs/contracts/FEATURE_CONTRACT_TEMPLATE.md`
- Migration design: `docs/superpowers/specs/2026-08-31-clean-product-migration-design.md`
