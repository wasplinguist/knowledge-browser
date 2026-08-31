# Feature contract: existing DB compatibility and ACL

## Status

Approved

## User outcome

Knowledge Browser can safely read the existing populated PostgreSQL database
without rebuilding source ingestion or regenerating a dataset.

## Evidence

The existing `knowledge_search` PostgreSQL 17 database is running with
pgvector 0.8.6. A read-only inspection on 2026-08-31 found 1,000 documents,
13,145 chunks, 16,520 embedded sentences, and 250 documents for each of Jira,
Confluence, Slack, and GitHub. Its document ACL distribution is 700
company-visible, 250 team-visible, and 50 named-user-visible records.

A focused live query confirmed that a member of an allowed group can see a
restricted document and an unrelated user cannot.

## Scope

- Database connection configuration through environment variables.
- A read-only compatibility check for the existing schema, extensions,
  partitions, indexes, relationships, populated data, and embeddings.
- Identity lookup by email or canonical user UUID.
- ACL-safe document reads with canonical IDs, source metadata, timestamps,
  URL, container, and provenance payload.
- ACL-safe chunk and sentence reads for later keyword and semantic retrieval.
- Focused tests for company, direct-user, group, missing, root, child, chunk,
  and sentence authorization.
- A small command-line compatibility check that prints only safe counts and
  status; it never prints credentials or company content.

## Non-goals

- Creating or migrating the production database schema.
- Slack, Jira, Confluence, or GitHub connectors.
- Crawling, synchronization, ingestion workers, re-indexing, or refresh jobs.
- Dataset generation or source rendering.
- Keyword search, semantic ranking, RRF, search API, RAG, or UI behavior.
- PostgreSQL RLS or production identity infrastructure.
- The exhaustive 52,800-pair ACL evaluation in normal local development.

## Dependencies

Only the runtime-foundation squash commit `5c432da` on `main` and access to the
already-populated database. The deferred canonical-dataset branch is not a
dependency.

## Connection contract

`DATABASE_URL` is preferred. If it is absent, the application uses
`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, and
`POSTGRES_PASSWORD`. Defaults target the existing local database:

- host `localhost`
- port `5432`
- database `knowledge_search`
- user `postgres`
- password `postgres` for local development only

The application must not log or return the connection URL or password.
Production read functions execute `SELECT` statements only.

## Existing schema contract

Required extensions:

- `pgcrypto`
- `vector`

Required identity and ACL tables:

- `users`
- `groups`
- `group_memberships`
- `permission_sets`
- `permission_set_users`
- `permission_set_groups`

Required content tables:

- `documents`
- `chunks` with Jira, Confluence, Slack, and GitHub partitions
- `sentences` with Jira, Confluence, Slack, and GitHub partitions

Required document fields include canonical UUID `id`, `source`, `kind`,
`external_id`, `parent_document_id`, `root_document_id`, `permission_set_id`,
`title`, `body`, `author`, `url`, `container`, `raw_payload`, source timestamps,
and `indexed_at`.

Required chunk fields include `source`, stable text `id`, `document_id`,
`field`, `text`, `chunk_index`, `content_hash`, `metadata`, and generated `fts`.

Required sentence fields include `source`, `id`, `chunk_id`,
`sentence_index`, `sentence`, `embedding halfvec(1536)`, and
`embedding_model`.

Required search indexes include the GIN full-text index `chunks_fts_idx` and
the HNSW vector index `sentences_embedding_idx`. Existing primary, unique, and
foreign-key constraints must remain valid.

Compatibility fails if required schema is missing, the database has no
documents/chunks/sentences, an expected source is absent, an embedding is
missing, a document root is broken, or a chunk is orphaned.

## ACL contract

Missing identities, permission sets, permission links, or ACL data fail
closed.

A document is visible only when both conditions are true:

1. Its own permission set allows the user.
2. Its root document permission set allows the user.

A permission set allows a user when its visibility is `company`, it directly
names the user, or it names a group containing the user.

The same two-part predicate must be applied before returning a document,
chunk, or sentence. A visible root cannot expose a hidden child. A visible
child cannot expose a hidden root. Forbidden and missing records both return
no record so callers cannot discover hidden content.

SQL identifiers used in reusable ACL predicates must be validated and cannot
come from request data. Values always use database parameters.

## Read interfaces

- `resolve_identity(conn, email) -> Identity | None`
- `get_document(conn, user_id, source, external_id) -> Document | None`
- `get_document_chunks(conn, user_id, source, external_id) -> list[Chunk]`
- `get_chunk_sentences(conn, user_id, source, chunk_id) -> list[Sentence]`
- `check_compatibility(conn) -> CompatibilityReport`

Returned records preserve canonical IDs and provenance fields. Raw ACL tables,
permission-set identifiers, group membership, internal database errors, and
credentials are never returned as content metadata.

## Safety invariants

- The existing populated database is inspected read-only.
- Local integration tests use a dedicated database whose name contains
  `_test`; they never truncate or write to `knowledge_search`.
- Unknown users receive no documents, chunks, or sentences.
- ACL filtering happens in SQL before content is returned.
- Focused ACL tests must report zero leaks.
- No ingestion or dataset step is needed to run the compatibility check.

## Quality and performance

The compatibility check and focused ACL suite should each finish in under one
minute locally. Reads use the existing keys and indexes and do not scan the
full ACL user-document matrix.

## Acceptance criteria

- The new package connects to the existing populated database.
- The compatibility command reports a compatible schema and safe aggregate
  counts without exposing content or credentials.
- Representative document, source metadata, URL, container, and provenance
  fields are preserved.
- Authorized users can read allowed documents, chunks, and sentences.
- Unauthorized and unknown users receive no record for the same IDs.
- Root/child tests prove zero leaks in both directions.
- Malformed SQL alias or parameter names are rejected.
- Focused unit and integration tests pass.
- Existing API and web checks remain green.
- No ingestion pipeline or dataset regeneration is required.

## Verification

- `api/.venv/bin/python -m pytest -q -m "unit or integration" api/tests`
- `DATABASE_URL=... api/.venv/bin/python -m knowledge_browser.db_compat`
- `npm test -- --run` from `web/`
- `npm run build` from `web/`
- `docker compose config --quiet`
- `git diff --check`

## Source reference

The schema, ACL predicate, safe document reads, and focused ACL behavior were
inspected in the old `knowledge-search` repository and against its running
database. The old repository and database remain read-only evidence. No
ingestion code, source connector, generated dataset, report, cache, local
setting, secret, or Git history is imported.
