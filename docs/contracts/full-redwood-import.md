# Feature contract: Full Redwood import

## Status

Approved by the user on 2026-09-02.

## User outcome

A developer can safely load and index the full local Redwood dataset in a
separate Docker database. An interrupted import continues from its last
completed batch instead of starting again, and the normal company database is
never changed.

## Evidence

- `data/redwood` contains 304,966 artifacts and is about 1.5 GB.
- 285,605 artifacts are Slack records.
- The existing importer reads every JSONL file, parsed document, unique
  sentence, and embedding into memory before one large transaction.
- A 100-document Redwood pilot loaded successfully into the isolated
  `knowledge_redwood` database with 2,894 chunks and 8,544 sentences.
- The user approved rebuilding the pilot database, using the OpenAI embedding
  API for the full corpus, and using a resumable batch design.

## Scope

- Add an opt-in Docker service and volume for the Redwood database on port
  5433 by default.
- Validate the full manifest and JSONL records with bounded memory.
- Import identities, groups, ACLs, documents, chunks, and sentence embeddings
  in restartable batches.
- Record import progress inside the Redwood database.
- Avoid repeated embedding calls across completed batches.
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

## Safety invariants

- Reset refuses any database whose name is not exactly `knowledge_redwood`.
- The normal database URL, container, and volume are never reset or written.
- Manifest validation completes before reset, API calls, or import writes.
- ACL mapping keeps company, group, and direct-user rules unchanged.
- Unknown and unauthorized users receive no protected search result.
- Progress advances only when the matching data batch commits.
- API keys, document text, embeddings, and passwords are never logged.
- External API calls contain only text required for embeddings.

## Quality and performance

Product-intent checklist:

1. Real problem: the current 1,000-document database cannot test or serve the
   complete Redwood company corpus.
2. Affected intents: known items, facts, ownership, project status, decisions,
   troubleshooting, cross-service evidence, aliases, recency, and tacit terms.
3. Evidence: the full local corpus has 304,966 artifacts; the 100-document
   pilot proved schema and embedding compatibility.
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
Evidence: 304,966 manifest-declared artifacts and a successful 100-document pilot
Affected intents: all supported company-knowledge retrieval intents
Metric: Redwood recall@10 and MRR, zero ACL leaks, and measured local search latency
Regression risk: lower precision or slower retrieval on the much larger corpus
Questions: none
```

Memory usage must stay bounded by metadata plus one document/embedding batch,
not total artifact count. Import status must update at least once per completed
batch. After indexes are built, focused local searches must complete within two
seconds at p95, excluding answer generation. OpenAI use is limited to the
configured embedding model and sentences not already stored in the resumable
import cache.

## Acceptance criteria

- A clean Redwood database imports exactly 304,966 documents from all four
  sources without changing the normal database.
- The process can be stopped after a committed batch and resumed without
  duplicate documents, chunks, sentences, permission mappings, or API work for
  cached embeddings.
- Invalid manifests, changed manifests, partial schemas, unsafe reset targets,
  provider errors, and database errors stop safely with actionable messages.
- Every imported sentence has a 1,536-dimension embedding using the released
  model.
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
- Focused compatibility, ACL, semantic-search, keyword-search, and hybrid-search
  checks. Do not run `full_acl`, `full_retrieval`, or `nightly`.
- Full normal API test suite, web test suite, web build, Compose validation, and
  `git diff --check`.
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

