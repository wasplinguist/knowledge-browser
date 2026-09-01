# Knowledge Browser

Knowledge Browser is a local company-knowledge search product for Slack, Jira,
Confluence, and GitHub content. It combines ACL-safe hybrid retrieval with
grounded AI answers and source-aware document views.

The repository includes the API, web app, PostgreSQL development service,
company dataset, released search profile, evaluation queries, and release
safety checks. Demo identity is used only to exercise document permissions; it
is not production authentication.

## Requirements

- Python 3.12
- Node.js 22.22.2 or newer
- Docker with Compose
- An OpenAI API key for first-run embeddings and grounded answers

## Install

Create the Python environment and install the web dependencies:

```bash
cd /path/to/knowledge-browser
python3.12 -m venv api/.venv
api/.venv/bin/python -m pip install -e './api[dev]'
(cd web && npm ci)
```

Create the local environment file and add your OpenAI API key:

```bash
cp .env.example .env
```

```dotenv
OPENAI_API_KEY=your-key-here
```

Do not commit `.env`.

## Run

From any directory, run:

```bash
/path/to/knowledge-browser/run_server.sh
```

On the first run, the script:

1. starts PostgreSQL with Docker Compose;
2. waits for the database to become ready;
3. creates the Knowledge Browser schema;
4. validates and imports the committed `data/company/` dataset;
5. creates embeddings and checks database compatibility;
6. starts the FastAPI and React/Vite development servers.

The first import creates 100 users, 1,000 documents, 13,145 chunks, and 16,520
sentences. Later runs verify and reuse that database instead of importing it
again.

Open:

- Web: <http://127.0.0.1:5173>
- API health: <http://127.0.0.1:8000/api/health>

Press `Ctrl+C` to stop the API and web servers. PostgreSQL continues running
until you run `docker compose down`.

### Database safety

First-run setup never resets or replaces data. It stops when it finds a
partially initialized or incompatible database. A compatible populated
database is reused without another import.

`DATABASE_URL` takes precedence when set. Otherwise the application uses the
`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, and
`POSTGRES_PASSWORD` values in `.env`. Already exported environment variables
override `.env`. When an explicit `DATABASE_URL` is supplied, startup does not
launch the project Docker database.

`API_PORT` and `WEB_PORT` change the local server ports. `ANSWER_MODEL` selects
the grounded-answer model. If a query embedding fails after setup, keyword
search remains available.

## Product behavior

Choose a user from the Demo user menu, then search across Slack, Jira,
Confluence, and GitHub. The selected identity controls which documents the API
may retrieve, open, and cite.

Search supports:

- keyword and semantic retrieval;
- exact Jira keys;
- reciprocal-rank fusion and one result per root document;
- freshness, source-authority, and primary-project reranking;
- source facets and local source-detail panels;
- grounded answers with ACL-safe citations.

Search snippets are leads rather than citable evidence. The answer workflow
must open a chunk before citing it, and the server validates permissions,
budgets, evidence state, and citations throughout the request.

## Architecture

The API package lives in `api/src/knowledge_browser/`, the React application in
`web/`, the database schema in `db/init/`, and the committed company data in
`data/company/`. The released retrieval configuration is
`search/profiles/released.json`.

Search applies ACL filtering inside SQL before ranking or reading content. The
same released retrieval profile is used for result lists and initial answer
evidence, so grounded answers cannot bypass search permissions.

The main API routes are:

- `GET /api/health`
- `GET /api/demo-users`
- `GET /api/search`
- `POST /api/answer`
- `GET /api/documents/{source}/{external_id}`
- `POST /api/search-events/{search_id}/click`

## Evaluation

Committed evaluation definitions live in `eval/`. Retrieval evaluation covers
known-item, semantic, multi-hop, temporal, alias, personalized, and negative
queries. ACL evaluation checks configured user/query pairs and requires zero
root or matched-child leaks.

These evaluations compare controlled Knowledge Browser search profiles. They
do not by themselves prove superiority over the native search products of
Slack, Jira, Confluence, or GitHub.

### Eval-driven development

Generate a fresh behavior report outside every Git worktree:

```bash
PYTHONPATH=api/src api/.venv/bin/python scripts/run_eval_loop.py analyze \
  --days 7 \
  --output-dir /tmp/knowledge-browser-behavior
```

Run a committed challenger from a clean worktree:

```bash
PYTHONPATH=api/src api/.venv/bin/python scripts/run_eval_loop.py evaluate \
  --experiment eval/experiments/<id>/experiment.json \
  --output-dir /tmp/knowledge-browser-runs/<id>
```

The loop produces evidence for human review. It never promotes a search profile
automatically.

## Verification

```bash
# Normal API checks; expensive release gates stay excluded
api/.venv/bin/python -m pytest -q \
  -m "not (full_acl or full_retrieval or nightly)" api/tests

# Web checks
(cd web && npm test -- --run)
(cd web && npm run build)

# Startup shell checks
scripts/test_setup_database.sh
scripts/test_run_server.sh

# Configuration and formatting checks
docker compose config --quiet
git diff --check
```

The exhaustive `full_retrieval` and `full_acl` groups are separate manual or
nightly release gates.

## Project rules

- Product intent: `docs/PRODUCT_INTENT.md`
- Feature contract template: `docs/contracts/FEATURE_CONTRACT_TEMPLATE.md`
- Repository workflow: `AGENTS.md`
