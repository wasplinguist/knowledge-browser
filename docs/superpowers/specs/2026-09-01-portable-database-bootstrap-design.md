# Portable Database Bootstrap Design

## Goal

Make a fresh Knowledge Browser clone runnable with one command. On the first
`./run_server.sh`, the project creates and fills its local PostgreSQL database
from the committed canonical company data. Later runs reuse that database.

The feature intentionally activates one previously deferred path: local
bootstrap from the old active, manifest-verified `data/company` dataset. It does
not reactivate dataset generation, source connectors, or continuous ingestion.

## Chosen approach

Commit the small source dataset and add a focused importer. This is preferred
over a database dump because the source remains reviewable and portable, and it
is preferred over the 252 MB embedding cache because the user approved creating
embeddings with `OPENAI_API_KEY` on first setup.

Only `data/company` moves into this repository. The old cache, reports, local
settings, experiments, and Git history do not move.

## Startup architecture

`run_server.sh` is the single developer entry point:

```text
load .env
   |
start Compose PostgreSQL
   |
wait for readiness (bounded)
   |
apply idempotent schema
   |
bootstrap command
   |-- documents exist -> skip import
   `-- empty -> validate manifest -> parse -> embed -> import transaction
   |
compatibility check
   |
start API and web app
```

The shell script only coordinates existing tools. Dataset rules and import
behavior live in Python, where they can be tested directly.

## Components

### Canonical source data

`data/company` contains the manifest, company truth records, ACL records,
evaluation truth records, and the four source artifact trees. The importer
checks every manifest hash and declared count before any OpenAI call or database
write.

The source data is immutable input. Runtime caches and reports stay ignored.

### Database schema

Versioned SQL under `db/init` defines the existing PostgreSQL 17 and pgvector
schema used by the product. The SQL is idempotent enough to apply during local
startup. It includes identity, ACL, document, chunk, sentence, analytics, and
evaluation tables and indexes already required by the merged compatibility and
search contracts.

Schema application does not drop tables or data.

### Bootstrap command

`python -m knowledge_browser.bootstrap --data data/company` owns setup
decisions. It connects with the normal database configuration and checks the
database state before importing.

State handling is explicit:

- No documents: import is allowed.
- Documents exist and compatibility passes: setup is complete; return success.
- Documents exist but compatibility fails: stop; do not repair or replace.
- Some import-owned tables contain partial data but documents are empty: stop;
  report a partial database instead of guessing that it is safe to overwrite.

The command prints only short progress and safe aggregate counts.

### Dataset parser and importer

The parser converts source JSONL into users, groups, memberships, permission
sets, documents, chunks, and sentences. Stable IDs, root-child relationships,
source metadata, timestamps, and ACL links must match the existing populated
database contract.

Import uses one database transaction. Provider calls happen only after manifest
validation. Unique sentence text is embedded in bounded batches and reused
inside the run. Database writes occur only after all required embeddings are
available, so provider failure cannot commit partial data.

Tests inject a deterministic fake embedder. Production uses the configured
OpenAI client and released embedding model.

### Server runner

`run_server.sh` keeps its current port cleanup and server shutdown behavior. It
adds Docker startup, readiness polling, schema application, bootstrap, and
compatibility verification before launching either server.

The script fails early with simple messages for missing Docker, missing `.env`,
missing API key during an empty bootstrap, PostgreSQL timeout, schema failure,
import failure, or compatibility failure.

## Data and ACL safety

The importer must preserve the source ACL model exactly. Every document,
including children, receives its intended permission set. Existing product
reads still require both the matched document and its root to allow the user.

Automatic setup never truncates or replaces an existing populated database.
The only automatic write path is an empty, compatible local database. Tests use
database names containing `_test` and never target `knowledge_search`.

Secrets are loaded from `.env` and exported to child processes, but never
printed. Error output does not include source content, embeddings, provider
payloads, connection URLs, or passwords.

## Repository rule update

Repository guidance changes narrowly: the committed `data/company` directory
is now the approved canonical bootstrap input. The ban remains for the old
embedding cache, generated reports, experiments, local settings, secrets,
inactive datasets, and copied Git history.

Canonical dataset generation remains deferred. Bootstrap consumes the approved
snapshot; it does not rebuild it.

## Error handling

- Invalid manifest or source file: stop before provider calls and writes.
- Missing API key on an empty database: explain that first setup needs the key.
- Embedding provider error: roll back and keep the database empty.
- PostgreSQL readiness timeout: stop and point to Compose logs.
- Existing incompatible data: stop and suggest the read-only compatibility
  command; never reset automatically.
- Partial database: stop and require explicit developer action.
- API or web startup failure: stop the sibling process through the existing
  cleanup trap.

## Testing

Test-first implementation covers:

- Manifest hashes, counts, missing files, and changed files.
- Source parsing, stable IDs, relationships, source metadata, and ACL mapping.
- Chunk and sentence determinism and embedding deduplication.
- Empty-database import using fake embeddings.
- Transaction rollback after a forced failure.
- Populated-database skip with zero embedder calls.
- Partial and incompatible database refusal.
- Expected aggregate counts and all four sources.
- Focused company, team, direct-user, root, child, and unknown-user ACL reads.
- Bootstrap CLI messages and exit behavior.
- Shell startup sequencing and repeat-run behavior.
- Existing focused API/search tests, web tests, and web build.

The exhaustive `full_acl`, `full_retrieval`, and `nightly` suites are explicitly
excluded at the user's request. No paid provider call is made by automated
tests.

## Delivery

The work stays on branch `codex/portable-db-bootstrap` in its own worktree. The
contract, tests, implementation, and data migration are committed as reviewable
steps. After verification, the branch is pushed and one focused pull request is
opened for squash merge.
