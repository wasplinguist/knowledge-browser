# Full Redwood import design

## Goal

Load all 304,966 local Redwood artifacts into a separate searchable PostgreSQL
database without holding the full corpus in memory. The import must survive an
interruption and continue without duplicate data or unnecessary repeated
embedding calls.

## Boundaries

The full import is opt-in and separate from the portable `data/company`
bootstrap. It never changes the normal `knowledge_search` database. Redwood
source files, temporary embedding data, progress output, and generated reports
remain local and ignored by Git.

## Components

### Opt-in Docker database

Add a Compose service behind a Redwood profile. It uses PostgreSQL 17 with
pgvector, port 5433 by default, database `knowledge_redwood`, and its own named
volume. Normal `docker compose up -d db` behavior does not change.

### Streaming dataset reader

Keep the current document mapping rules, but separate small shared context from
artifact iteration. Employees, teams, projects, and ACL principals are loaded
as context. Artifact JSONL files are hashed, counted, validated, and parsed one
line at a time. Validation makes one complete pass before reset or external API
use. Import makes a second streaming pass.

### Import state

Bulk-import support tables store:

- manifest digest and dataset version;
- embedding model and dimensions;
- current source file and next line number;
- committed counts and timestamps;
- the last safe error summary;
- a sentence-hash embedding cache used during import;
- deterministic permission-set mappings.

State and imported rows commit together for each document batch. A changed
manifest, model, or completed-run configuration cannot resume accidentally.

### Batched writer

Identity and group rows use PostgreSQL bulk operations. Documents use stable
IDs derived from source and external ID. Existing deterministic chunk IDs are
kept. Each document batch collects its unique uncached sentences, requests
embeddings in provider-sized batches, stores the cache, and bulk-inserts
documents, chunks, and sentences. Conflict handling is idempotent and never
silently replaces different source content.

### Deferred indexes

The clean Redwood schema loads without the GIN text index and HNSW vector index.
After every artifact batch is complete, the importer creates both indexes and
runs `ANALYZE`. Search is enabled only after index creation and final
verification. A resumed incomplete run does not rebuild indexes early.

### Operator commands

One small command surface provides `validate`, `reset`, `run`, `status`, and
`verify`. Reset requires both an explicit flag and the exact database name
`knowledge_redwood`. Status is read-only. Run resumes by default when valid
state exists.

## Data flow

1. Hash and validate every manifest file with streaming reads.
2. Load and validate identity/project context.
3. Validate every artifact line and cross-reference without storing the corpus.
4. On explicit reset, recreate only the Redwood database schema and import
   support tables without search indexes.
5. Bulk-load users, groups, memberships, and ACL mappings.
6. For each source, seek to its saved next line and read one document batch.
7. Map documents and ACLs, find uncached sentence hashes, and request missing
   embeddings.
8. Commit cache entries, searchable rows, and the next line together.
9. Repeat until all manifest counts match.
10. Build indexes, analyze tables, mark the run complete, and verify.

## Failure and resume behavior

Manifest or cross-reference errors happen before reset. Provider calls use
bounded retries for transient failures. A permanent provider or database error
stops the command and records only a safe summary. An uncommitted batch is
retried. A committed batch is skipped through its saved line checkpoint.

The importer refuses to resume if the dataset hash, embedding model, vector
dimensions, or batch semantics differ. It also refuses populated databases
that do not contain matching import state.

## Safety

All searchable rows keep the existing permission-set model. Focused tests cover
company, group, direct-user, and unknown-user behavior. Logs show counts,
source positions, durations, and retry reasons, but not document text, vectors,
keys, or passwords.

## Quality and performance

The final database must contain exactly the manifest artifact count and all
four sources. Every sentence must have an embedding using the released model.
The 274 Redwood questions provide an external quality check; report recall@10
and MRR without changing the questions or ranking profile. Measure p50 and p95
search latency, with a two-second p95 target for focused local search excluding
answer generation.

The importer holds only context plus one batch in memory. Batch size is
configurable, with a conservative default. Indexes are built once after the
load to avoid per-row HNSW maintenance.

## Testing

Unit tests cover streaming validation, parsing, progress rules, reset guards,
embedding-cache reuse, and idempotent row identities. Integration tests use a
small temporary manifest, fake embeddings, and a dedicated `_test` database.
One test interrupts after a committed batch and proves a resume reaches the
same final counts without duplicates or repeat calls for cached sentences.

Normal API and web checks still run. Exhaustive `full_acl`, `full_retrieval`,
and `nightly` suites do not run. The manual full import verifies exact counts,
indexes, compatibility, focused ACL behavior, retrieval quality, latency, and
the unchanged 1,000-document normal database.

## Delivery

The contract, tests, implementation, and operator documentation stay in one
feature branch and one pull request. After review and green CI, the pull request
is squash-merged. The full local import then runs against the separate Redwood
container and remains resumable until verification completes.
