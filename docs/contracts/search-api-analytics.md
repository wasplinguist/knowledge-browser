# Feature contract: search API and analytics

## Status

Implemented.

## User outcome

The demo web client can search with an explicit demo identity, receive stable
root results and source facets, and record valid result clicks.

## Scope

- `GET /api/search` over the shared Feature 3 hybrid pipeline.
- `GET /api/demo-users` for clearly labelled local demo identity selection.
- Source filters for Jira, Confluence, Slack, and GitHub.
- A stable response containing profile, items, facets, and optional search ID.
- Best-effort search events containing only query metadata and displayed root
  identities.
- Owner-only, rank-checked click events.
- Small consistent API errors.

## Non-goals

- Real login, production identity, RAG answers, document panels, UI changes,
  ingestion, or ranking experiments.
- Calling an embedding provider when no embedder is configured. Keyword search
  remains available and an injected embedder enables hybrid retrieval.

## Safety and behavior

- A missing, malformed, or unknown demo identity cannot search or record a
  click.
- The API uses Feature 3 results and never performs a second unfiltered search.
- Embedding failure falls back to keyword results.
- Analytics failure never turns valid search results into an error.
- Search events store at most ten ordered `{source, external_id}` root
  identities, not chunks, excerpts, ACL data, or document bodies.
- A click is accepted only for the event owner and the exact identity stored at
  the supplied one-based rank.

## Acceptance criteria

- Search validation, facets, fallback, event storage, and click ownership tests
  pass.
- Existing retrieval and ACL tests remain green.
- The API suite finishes in under one minute locally.

## Verification

- `api/.venv/bin/python -m pytest -q -m "unit or integration" api/tests`
- `npm test -- --run` from `web/`
- `npm run build` from `web/`
- `docker compose config --quiet`
- `git diff --check`
