# Feature contract: portable database bootstrap

## Status

Approved

## User outcome

A developer can clone Knowledge Browser onto a new computer and run
`./run_server.sh`. The command starts PostgreSQL, creates the required schema,
imports the canonical company dataset when the database is empty, verifies the
database, and then starts the API and web app. Later runs reuse the populated
database without importing again.

## Evidence

The current product can read the populated local `knowledge_search` database,
but a new computer has no portable way to create that database. The user asked
to bring the original company data into this project and initialize the
database on the first server run.

The old active dataset is manifest-verified and contains 100 users, 1,000
documents, 13,145 chunks, and 16,520 sentences after indexing. Its neighboring
embedding cache is 252 MB and is not required when embeddings are created with
the configured OpenAI API key.

## Scope

- Commit only the old active `data/company` source dataset, including its
  manifest and source artifacts.
- Add the PostgreSQL schema required by the existing compatibility contract.
- Add manifest validation, source parsing, chunking, embedding, and transactional
  import code needed for this dataset.
- Add an idempotent database setup command for an empty local database.
- Update `run_server.sh` to load `.env`, start PostgreSQL, wait for readiness,
  run setup, verify compatibility, and start the API and web app.
- Update repository guidance to allow this one canonical source dataset while
  continuing to reject caches, generated reports, local settings, and inactive
  datasets.

## Non-goals

- Copying `data/.cache`, especially the 252 MB embedding cache.
- Copying old reports, experiments, local settings, secrets, or Git history.
- Regenerating the canonical dataset.
- Adding Slack, Jira, GitHub, or Confluence connectors or sync workers.
- Replacing or re-importing a database that already contains documents.
- Running the exhaustive `full_acl` or `full_retrieval` suites in this feature.
- Production database migration, backup, restore, or high-availability tooling.

## Dependencies

- Latest remote `main` at `be5da7f` or later.
- Docker with Compose, Python 3.12, and Node.js 22.22.2 or later.
- A valid `OPENAI_API_KEY` for first-run embedding creation.
- The existing database compatibility, ACL-safe reads, hybrid retrieval, search
  API, grounded RAG, and web experience already merged into `main`.

The feature does not depend on the old unmerged `codex/canonical-dataset`
worktree or branch.

## Interface and data contract

`./run_server.sh` remains the developer entry point. It must:

1. Load `.env` without printing secrets.
2. Start the Compose `db` service.
3. Wait a bounded time for PostgreSQL readiness.
4. Apply idempotent schema SQL.
5. Run `python -m knowledge_browser.bootstrap --data data/company`.
6. Run the existing compatibility check.
7. Start the API on port 8000 and web app on port 5173, after freeing those
   ports as already requested.

The bootstrap command accepts a dataset directory and uses the normal database
environment variables. Its outcomes are:

- Empty database: validate the manifest, import all records and ACLs, create
  chunks and embeddings, then commit.
- Database with documents: report that setup is already complete and make no
  data changes.
- Partial, invalid, or incompatible database: exit nonzero with a safe,
  actionable message; do not delete or replace data.
- Missing or invalid dataset, missing API key, provider failure, or database
  failure: exit nonzero and leave no partial import committed.

The committed dataset must match its manifest. Expected indexed counts are 100
users, 1,000 documents, 13,145 chunks, and 16,520 sentences.

## Safety invariants

- Existing databases with documents are never truncated, replaced, or
  re-imported automatically.
- Import is transactional; a failed import does not leave partial searchable
  data.
- ACL source records produce the same company, group, and direct-user access
  rules required by the existing ACL contract.
- Unknown users and disallowed users receive no protected document, chunk, or
  sentence.
- API keys, database passwords, embeddings, and source content are not logged
  as error diagnostics.
- Tests write only to a dedicated database whose name contains `_test`.
- The full ACL matrix is not run; focused ACL tests must still prove company,
  group, direct-user, root, child, and unknown-user behavior.

## Quality and performance

Manifest validation must happen before provider calls or database writes.
Normal later runs must skip import and reach server startup without OpenAI API
calls. First-run cost is bounded to the unique sentences in the canonical
dataset, with batching and deduplication matching the source import behavior.

This feature does not change retrieval or ranking behavior, so no search intent
audit or new quality challenger is required. The populated database must pass
the existing compatibility check and preserve the released search behavior.

## Acceptance criteria

- A clean local PostgreSQL volume can be populated by `./run_server.sh`.
- The manifest is verified before import.
- First setup requires and uses `OPENAI_API_KEY` for embeddings.
- A second setup makes no import or embedding provider calls.
- Existing populated databases are not changed.
- Invalid, partial, and failed imports stop safely with clear messages.
- Compatibility reports 100 users, 1,000 documents, 13,145 chunks, and 16,520
  sentences with all four sources present.
- Focused ACL tests show no leak across representative company, team, direct,
  root, child, and unknown-user cases.
- API and web tests remain green.
- No embedding cache, report, secret, or inactive dataset is committed.

## Verification

- Unit tests for manifest validation, parsing, chunking, and setup decisions.
- Integration import into a dedicated `_test` database with deterministic fake
  embeddings.
- Repeat-run integration test proving no second import.
- Focused compatibility, database, ACL, search, and API tests.
- Web test suite and production build.
- `docker compose config --quiet`.
- `git diff --check`.
- Manual first-run smoke test using a disposable local database name and
  volume.

Do not run tests marked `full_acl`, `full_retrieval`, or `nightly` for this
feature.

## Source reference

The following old-repository paths are approved source references for this
focused migration:

- `data/company/` — the active manifest-verified dataset; this directory alone
  is copied.
- `db/init/001_schema.sql` and `db/init/002_eval.sql` — schema reference.
- `api/src/knowledge_search/ingest.py` — parsing and transactional import
  reference.
- `api/src/knowledge_search/embeddings.py` — chunking, deduplication, and
  embedding batching reference.
- `scripts/dev.sh` — local startup sequencing reference.

Old Git history, `data/.cache`, generated reports, secrets, local settings, and
other inactive data remain reference-only and must not be imported.
