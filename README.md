# Knowledge Browser

Knowledge Browser is an ACL-safe company knowledge search product being rebuilt
through approved feature contracts.

## Current scope

The runtime foundation contains a health-only FastAPI service, a minimal React
shell, PostgreSQL 17 with pgvector for local development, and CI. Search, RAG,
data, database tables, and ACL behavior arrive in later contract-driven PRs.

## Requirements

- Python 3.12
- Node.js 22
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

```bash
docker compose up -d db
docker compose ps
```

Use a different `POSTGRES_PORT` for each parallel worktree.

## Verification

```bash
api/.venv/bin/python -m pytest -q api/tests
(cd web && npm test -- --run)
(cd web && npm run build)
docker compose config --quiet
git diff --check
```

## Product and contribution rules

- Product guardrails: `docs/PRODUCT_INTENT.md`
- Feature contract template: `docs/contracts/FEATURE_CONTRACT_TEMPLATE.md`
- Migration design: `docs/superpowers/specs/2026-08-31-clean-product-migration-design.md`
