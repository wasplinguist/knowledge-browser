# Feature contract: Full Redwood import

## Status

Approved by the user on 2026-09-02.

## User outcome

A developer can safely load and index the full local Redwood dataset in a
separate Docker database. An interrupted import continues from its last
completed batch instead of starting again, and the normal company database is
never changed.

## Evidence

- The current manifest contains 49,448 artifacts, including 30,087 Slack
  records, and has digest
  `b51f0c6732d944969f592c769ebd394471eb3c7ee4ebf0addd647bb7865c9a43`.
- The stopped import has a valid committed Slack checkpoint at 800 documents,
  41,914 chunks, and 70,252 sentences. The handoff observed 700 documents
  before the final in-flight transaction committed; the newer database
  checkpoint is authoritative and must not be reset.
- The existing importer reads every JSONL file, parsed document, unique
  sentence, and embedding into memory before one large transaction.
- A 100-document Redwood pilot loaded successfully into the isolated
  `knowledge_redwood` database with 2,894 chunks and 8,544 sentences.
- The user approved using the OpenAI embedding API for the full corpus, the
  resumable batch design, and the throughput architecture that moves provider
  latency outside transactions, uses bounded concurrency and token-aware
  requests, and bulk-persists embedding cache rows.

## Scope

- Add an opt-in Docker service and volume for the Redwood database on port
  5433 by default.
- Validate the full manifest and JSONL records with bounded memory.
- Import identities, groups, ACLs, documents, chunks, and sentence embeddings
  in restartable batches.
- Record import progress inside the Redwood database.
- Avoid repeated embedding calls across completed batches.
- Deduplicate sentences across a configurable bounded document window.
- Run bounded concurrent, token-aware embedding requests outside database
  transactions with explicit timeouts and bounded transient retries.
- Persist embedding-cache misses with a PostgreSQL bulk operation and
  set-based collision checks.
- Build the text and vector indexes after the data load completes.
- Add commands to start, reset, import, resume, inspect, and verify the Redwood
  database.
- Run focused Redwood quality, latency, compatibility, and ACL checks.

## Non-goals

- Do not commit `data/redwood`, generated samples, embedding caches, reports,
  credentials, or database files.
- Do not change the released ranking profile or answer-generation behavior.
- Do not replace or modify the normal `knowledge_search` database.
- Do not add connectors, scheduled ingestion, an admin UI, or production
  migration tooling.
- Do not run the exhaustive `full_acl`, `full_retrieval`, or `nightly` suites.

## Dependencies

- Existing database schema and ACL-safe reads.
- Existing hybrid retrieval, search API, grounded RAG, and web experience.
- The local manifest-verified Redwood source dataset.
- Docker, PostgreSQL with pgvector, and a configured OpenAI API key.

## Interface and data contract

The opt-in Redwood workflow uses a separate database:

- Container/service: `redwood-db`
- Database: `knowledge_redwood`
- Host port: `${REDWOOD_POSTGRES_PORT:-5433}`
- Persistent volume: `knowledge_redwood_data`

The import command accepts a dataset path, database URL, and batch size. It
supports these explicit operations:

- `validate`: verify hashes, counts, JSON shapes, references, and duplicate IDs
  without changing the database.
- `reset`: delete only an explicitly named Redwood database after successful
  validation and initialize an empty bulk-load schema.
- `run`: start or continue the import.
- `status`: report manifest identity, source position, document/chunk/sentence
  counts, failed batch information, and completion state.
- `verify`: check compatibility, exact source counts, embeddings, ACL safety,
  retrieval, and index presence.

Every committed batch stores its source file and next line number in the same
database transaction as its imported rows. A retry starts at that line.
Completed records use deterministic identities or uniqueness constraints, so a
retried batch cannot create duplicates. The manifest digest and embedding model
are fixed for one run; a mismatch stops with a clear error.

Provider work follows three separate phases. A short transaction reads the
checkpoint and embedding cache, all HTTP requests run with no database
transaction open, a second short transaction bulk-persists new embeddings,
and each document batch advances its checkpoint atomically with its document,
chunk, and sentence rows. A crash after cache persistence reuses those vectors;
a crash before checkpoint commit safely replays the document batch.

## Safety invariants

- Reset refuses any database whose name is not exactly `knowledge_redwood`.
- The normal database URL, container, and volume are never reset or written.
- Manifest validation completes before reset, API calls, or import writes.
- ACL mapping keeps company, group, and direct-user rules unchanged.
- Unknown and unauthorized users receive no protected search result.
- Progress advances only when the matching data batch commits.
- API keys, document text, embeddings, and passwords are never logged.
- External API calls contain only text required for embeddings.
- Provider calls never run while a database transaction is open.
- Request concurrency, input count, estimated token budget, retry count, and
  timeouts have conservative defaults and hard upper bounds.

## Quality and performance

Product-intent checklist:

1. Real problem: the current 1,000-document database cannot test or serve the
   complete Redwood company corpus.
2. Affected intents: known items, facts, ownership, project status, decisions,
   troubleshooting, cross-service evidence, aliases, recency, and tacit terms.
3. Evidence: the current local corpus has 49,448 artifacts; the 100-document
   pilot and preserved checkpoint prove schema and embedding compatibility.
4. Target metric: the expected document appears in the top 10 for accessible
   questions in `qa.jsonl`, with zero ACL leaks. Report recall@10 and MRR.
5. Regression risk: the larger corpus can reduce precision and increase search
   latency.
6. Golden-set risk: no ranking profile or QA expectation is changed to improve
   a score.
7. Unclear purpose: none.

Intent auditor verdict:

```text
Verdict: ALIGNED
Evidence: 49,448 manifest-declared artifacts, a successful pilot, and a preserved checkpoint
Affected intents: all supported company-knowledge retrieval intents
Metric: Redwood recall@10 and MRR, zero ACL leaks, and measured local search latency
Regression risk: lower precision or slower retrieval on the much larger corpus
Questions: none
```

Memory usage must stay bounded by metadata plus one configurable work window,
not total artifact count, and remain below 2 GB on the fixed-slice benchmark.
On the same fixed Redwood slice with an empty cache, the new embedding pipeline
must deliver at least 5x the legacy sentence throughput, with 10x as the target,
while producing the identical normalized sentence set and vector association.
The benchmark must also prove that provider latency leaves no transaction open.
Import status must update at least once per completed batch and report documents,
chunks, sentences, cache hits and misses, provider requests, configured
concurrency, retries, active throughput, and estimated remaining time. Status
must distinguish running, stalled, failed, indexing, and complete. After indexes
are built, focused local searches must complete within two seconds at p95,
excluding answer generation. OpenAI use is limited to the configured embedding
model and sentences not already stored in the resumable import cache.

## Acceptance criteria

- The preserved Redwood database imports exactly 49,448 documents from all four
  sources without changing the normal database.
- The process can be stopped after a committed batch and resumed without
  duplicate documents, chunks, sentences, permission mappings, or API work for
  cached embeddings.
- Invalid manifests, changed manifests, partial schemas, unsafe reset targets,
  provider errors, and database errors stop safely with actionable messages.
- Every imported sentence has a 1,536-dimension embedding using the released
  model.
- No HTTP request runs while a PostgreSQL transaction is open; transient 429,
  timeout, connection-reset, and 5xx failures retry within fixed bounds, while
  invalid indexes or vector dimensions stop safely.
- The fixed-slice benchmark reports at least 5x higher sentence throughput,
  less than 2 GB peak memory, identical normalized sentence/vector association,
  and successful stop/resume without repeated provider work.
- Final text and HNSW indexes exist and are valid.
- Compatibility reports no issues.
- Focused company, group, and unknown-user ACL checks pass. Direct-user checks
  pass when the validated source data contains direct-user ACLs; otherwise the
  report marks that source shape as explicitly not applicable and requires
  zero `permission_set_users` database links.
- Redwood QA metrics and p50/p95 search latency are recorded for review.
- No Redwood source data or generated artifact is added to Git.

## Verification

- Unit tests for streaming manifest checks, line iteration, checkpoints,
  deterministic records, resume rules, and safe reset guards.
- Integration import with a small manifest and deterministic fake embeddings
  in a database whose name contains `_test`.
- Stop-and-resume integration test proving idempotence and no duplicate API
  requests for committed batches.
- Tests for transaction-free provider calls, deterministic concurrent ordering,
  token-aware batching, window deduplication, bulk cache persistence, bounded
  retries, stalled-request detection, and bounded representative-window memory.
- Focused compatibility, ACL, semantic-search, keyword-search, and hybrid-search
  checks. Do not run `full_acl`, `full_retrieval`, or `nightly`.
- Full normal API test suite, web test suite, web build, Compose validation, and
  `git diff --check`.
- Fixed-slice old/new benchmark with an empty cache and no generated output in
  Git.
- Manual full Redwood import with exact counts, index inspection, QA report,
  latency report, and confirmation that the normal database still has 1,000
  documents.

## Implementation inputs

- `data/redwood/**` as ignored local input only.
- `data/redwood/manifest.json` for file hashes and expected counts.
- `api/src/knowledge_browser/dataset.py` for record validation and mapping.
- `api/src/knowledge_browser/importer.py` and
  `api/src/knowledge_browser/embedding_index.py` for current import semantics.
- `db/init/001_schema.sql` for the searchable schema.
- `compose.yaml` and scripts under `scripts/` for opt-in operation.
- `search/profiles/released.json` for the embedding model.
