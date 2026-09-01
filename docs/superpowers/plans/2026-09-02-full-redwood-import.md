# Full Redwood Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely index all 304,966 local Redwood artifacts in a separate, resumable PostgreSQL database.

**Architecture:** A streaming reader validates artifacts without retaining the corpus. A database-backed coordinator imports deterministic rows and OpenAI embeddings in atomic batches, stores progress and an embedding cache, and builds GIN and HNSW indexes only after loading finishes.

**Tech Stack:** Python 3.12, psycopg 3, PostgreSQL 17, pgvector `halfvec(1536)`, Docker Compose, OpenAI `text-embedding-3-small`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-02-full-redwood-import-design.md`

## Global Constraints

- Keep `data/redwood/**`, samples, caches, reports, secrets, and database files outside Git.
- Never import into, reset, or otherwise change `knowledge_search`.
- Reset only a parsed database name exactly equal to `knowledge_redwood`, after full validation.
- Preserve the released search profile and all existing ACL behavior.
- Embeddings use `text-embedding-3-small` with 1,536 dimensions.
- Use `full_redwood_import_test` for integration tests in this worktree.
- Do not run `full_acl`, `full_retrieval`, or `nightly`.
- Follow red-green TDD and make one reviewable commit per task.

---

### Task 1: Stream Dataset Validation and Documents

**Files:**
- Modify: `api/src/knowledge_browser/dataset.py`
- Modify: `api/tests/test_dataset.py`
- Create: `api/tests/test_bulk_dataset.py`

**Interfaces:**
- Produces `ValidatedDataset(root: Path, manifest: dict, manifest_digest: str, context: dict)`.
- Produces `ArtifactRecord(source: str, line_number: int, start_offset: int, next_offset: int, document: ParsedDocument)`.
- Produces `validate_streaming_dataset(data_dir: Path) -> ValidatedDataset`.
- Produces `iter_artifacts(dataset, source, start_offset=0, start_line=1) -> Iterator[ArtifactRecord]`.
- Preserves `validate_manifest()` and `load_dataset()` for the portable bootstrap.

- [ ] **Step 1: Write failing streaming tests**

```python
def test_streaming_validation_does_not_read_whole_artifact_files(monkeypatch):
    original = Path.read_bytes
    def guarded(path):
        if "artifacts" in path.parts:
            raise AssertionError("artifact files must be streamed")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", guarded)
    assert validate_streaming_dataset(DATASET).manifest["counts"]["artifacts"] == 1000


def test_iterator_resumes_at_saved_offset():
    validated = validate_streaming_dataset(DATASET)
    records = iter_artifacts(validated, "jira")
    first, second = next(records), next(records)
    resumed = next(iter_artifacts(validated, "jira", first.next_offset, second.line_number))
    assert resumed.document.external_id == second.document.external_id
```

Also add a fixture with the same artifact ID in two source files and assert `ValueError("duplicate artifact ID")`.

- [ ] **Step 2: Run tests and verify red**

Run: `PYTHONPATH=.:api/src /Users/mac/workspace/knowledge-browser/api/.venv/bin/pytest -c api/pyproject.toml -q api/tests/test_bulk_dataset.py api/tests/test_dataset.py`

Expected: import errors for the four new public symbols.

- [ ] **Step 3: Implement bounded-memory validation**

```python
def _stream_sha256(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(block_size):
            digest.update(block)
    return digest.hexdigest()
```

Load only employees, teams, projects, and ACL context in memory. Validate artifact JSON one line at a time. Use a temporary SQLite table with a unique `external_id` column for cross-file duplicate detection, then delete it automatically. Make `load_dataset()` collect the iterator only for the small existing bootstrap.

- [ ] **Step 4: Run the Step 2 command and verify green**

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add api/src/knowledge_browser/dataset.py api/tests/test_dataset.py api/tests/test_bulk_dataset.py
git commit -m "Stream large dataset validation"
```

---

### Task 2: Add Safe Import State and Reset Guard

**Files:**
- Create: `db/init/002_bulk_import.sql`
- Create: `api/src/knowledge_browser/bulk_state.py`
- Create: `api/tests/test_bulk_state.py`
- Modify: `api/tests/test_bootstrap_schema.py`

**Interfaces:**
- Produces `BulkRun`, `Progress`, and `BulkStateError` data types.
- Produces `assert_redwood_database(database_url: str) -> None`.
- Produces `reset_redwood_database(database_url: str, schema_paths: Sequence[Path]) -> None`.
- Produces `start_or_resume_run(conn, validated, model, dimensions) -> BulkRun`.
- Produces `load_progress(conn, run_id, source) -> Progress` and `save_progress(...)`.

- [ ] **Step 1: Write failing safety and atomicity tests**

```python
@pytest.mark.parametrize("name", ["knowledge_search", "postgres", "knowledge_redwood_test"])
def test_reset_guard_refuses_every_other_database(name):
    with pytest.raises(BulkStateError, match="exactly knowledge_redwood"):
        assert_redwood_database(f"postgresql://postgres:postgres@localhost/{name}")


def test_progress_and_rows_rollback_together(db, run):
    with pytest.raises(RuntimeError):
        with db.transaction():
            save_progress(db, run.id, "slack", next_line=2, next_offset=100)
            db.execute("INSERT INTO users (email,name) VALUES ('rollback@test','Rollback')")
            raise RuntimeError("stop")
    assert load_progress(db, run.id, "slack").next_line == 1
    assert db.execute("SELECT count(*) FROM users WHERE email='rollback@test'").fetchone() == (0,)
```

- [ ] **Step 2: Run tests and verify red**

Run: `TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/full_redwood_import_test PYTHONPATH=.:api/src /Users/mac/workspace/knowledge-browser/api/.venv/bin/pytest -c api/pyproject.toml -q api/tests/test_bulk_state.py api/tests/test_bootstrap_schema.py`

Expected: missing module and schema-table failures.

- [ ] **Step 3: Add concrete state tables**

```sql
CREATE TABLE bulk_import_runs (
  id uuid PRIMARY KEY,
  manifest_digest text NOT NULL UNIQUE,
  dataset_version text NOT NULL,
  embedding_model text NOT NULL,
  embedding_dimensions integer NOT NULL CHECK (embedding_dimensions = 1536),
  status text NOT NULL CHECK (status IN ('loading','indexing','complete','failed')),
  safe_error text,
  started_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);
CREATE TABLE bulk_import_progress (
  run_id uuid NOT NULL REFERENCES bulk_import_runs(id) ON DELETE CASCADE,
  source text NOT NULL CHECK (source IN ('jira','confluence','slack','github')),
  next_line bigint NOT NULL DEFAULT 1,
  next_offset bigint NOT NULL DEFAULT 0,
  documents bigint NOT NULL DEFAULT 0,
  chunks bigint NOT NULL DEFAULT 0,
  sentences bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, source)
);
CREATE TABLE bulk_embedding_cache (
  run_id uuid NOT NULL REFERENCES bulk_import_runs(id) ON DELETE CASCADE,
  content_hash text NOT NULL,
  sentence text NOT NULL,
  embedding halfvec(1536) NOT NULL,
  PRIMARY KEY (run_id, content_hash)
);
```

Reject a changed manifest, model, dimension, partially initialized database, or populated database without matching state. Parse the URL with psycopg; never compare by substring.

- [ ] **Step 4: Run the Step 2 command and verify green**

- [ ] **Step 5: Commit**

```bash
git add db/init/002_bulk_import.sql api/src/knowledge_browser/bulk_state.py api/tests/test_bulk_state.py api/tests/test_bootstrap_schema.py
git commit -m "Add resumable bulk import state"
```

---

### Task 3: Batch Identities and Permissions

**Files:**
- Create: `api/src/knowledge_browser/bulk_writer.py`
- Create: `api/tests/test_bulk_writer.py`
- Modify: `api/src/knowledge_browser/importer.py`

**Interfaces:**
- Produces `IdentityMaps(users: dict[str, UUID], groups: dict[str, UUID])`.
- Produces `stable_uuid(kind: str, key: str) -> UUID`.
- Produces `import_identities(conn, context, page_size=1000) -> IdentityMaps`.
- Produces `permission_id(acl) -> UUID` and `ensure_permissions(conn, acls, identities)`.

- [ ] **Step 1: Write failing deterministic and idempotence tests**

```python
def test_stable_ids_are_repeatable():
    assert stable_uuid("document", "jira:ABC-1") == stable_uuid("document", "jira:ABC-1")
    assert stable_uuid("document", "jira:ABC-1") != stable_uuid("document", "jira:ABC-2")


def test_identity_import_is_idempotent(db, validated_dataset):
    first = import_identities(db, validated_dataset.context, page_size=10)
    second = import_identities(db, validated_dataset.context, page_size=10)
    assert first == second
    assert db.execute("SELECT count(*) FROM users").fetchone() == (len(first.users),)
```

Add one test that checks company, group, and direct-user permission links exactly.

- [ ] **Step 2: Run tests and verify red**

Run: `TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/full_redwood_import_test PYTHONPATH=.:api/src /Users/mac/workspace/knowledge-browser/api/.venv/bin/pytest -c api/pyproject.toml -q api/tests/test_bulk_writer.py`

Expected: `knowledge_browser.bulk_writer` is missing.

- [ ] **Step 3: Implement deterministic bulk writes**

```python
NAMESPACE = UUID("5f975176-6ea4-4f55-a1f8-b04f0ec25112")

def stable_uuid(kind: str, key: str) -> UUID:
    return uuid5(NAMESPACE, f"{kind}:{key}")

def permission_id(acl: dict[str, Any] | None) -> UUID:
    _, digest = _acl_key(acl)
    return stable_uuid("permission", digest)
```

Use `executemany()` and `ON CONFLICT DO NOTHING` for users, groups, memberships, permission sets, and permission links. Read final maps by email and group name. Keep the small importer unchanged.

- [ ] **Step 4: Run writer and bootstrap tests**

Run: `TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/full_redwood_import_test PYTHONPATH=.:api/src /Users/mac/workspace/knowledge-browser/api/.venv/bin/pytest -c api/pyproject.toml -q api/tests/test_bulk_writer.py api/tests/test_bootstrap.py`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add api/src/knowledge_browser/bulk_writer.py api/src/knowledge_browser/importer.py api/tests/test_bulk_writer.py
git commit -m "Bulk import identities and permissions"
```

---

### Task 4: Resume Document and Embedding Batches

**Files:**
- Modify: `api/src/knowledge_browser/bulk_writer.py`
- Create: `api/src/knowledge_browser/bulk_import.py`
- Create: `api/tests/test_bulk_import.py`
- Modify: `api/src/knowledge_browser/embedding_index.py`
- Modify: `api/tests/test_embedding_index.py`

**Interfaces:**
- Produces `BatchReport(documents, chunks, sentences, next_line, next_offset, provider_calls)`.
- Produces `embed_missing(conn, run_id, client, model, sentences, request_size=100)`.
- Produces `write_document_batch(conn, run, records, identities, embeddings)`.
- Produces `run_import(connection_factory, dataset, client_factory, document_batch_size=100, embedding_batch_size=100, stop_after_batches=None)`.

- [ ] **Step 1: Write failing cache and resume tests**

```python
def test_embedding_cache_avoids_repeat_calls(db, run, fake_client):
    first = embed_missing(db, run.id, fake_client, "text-embedding-3-small", ["same sentence"])
    second = embed_missing(db, run.id, fake_client, "text-embedding-3-small", ["same sentence"])
    assert first == second
    assert fake_client.calls == 1


def test_resume_finishes_without_duplicates(connection_factory, tiny_dataset):
    partial = run_import(connection_factory, tiny_dataset, FakeEmbeddingClient, document_batch_size=2, stop_after_batches=1)
    assert partial.complete is False
    final = run_import(connection_factory, tiny_dataset, FakeEmbeddingClient, document_batch_size=2)
    assert final.complete is True
    with connection_factory() as conn:
        duplicates = conn.execute("SELECT source,external_id FROM documents GROUP BY source,external_id HAVING count(*)>1").fetchall()
    assert duplicates == []
```

- [ ] **Step 2: Run tests and verify red**

Run: `TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/full_redwood_import_test PYTHONPATH=.:api/src /Users/mac/workspace/knowledge-browser/api/.venv/bin/pytest -c api/pyproject.toml -q api/tests/test_bulk_import.py api/tests/test_embedding_index.py`

Expected: resumable import symbols are missing.

- [ ] **Step 3: Implement the persistent embedding cache**

```python
def sentence_key(sentence: str) -> str:
    return hashlib.sha256(sentence.encode()).hexdigest()

def encoded_vector(vector: Sequence[float]) -> str:
    if len(vector) != 1536:
        raise ValueError("embedding provider returned invalid dimensions")
    return "[" + ",".join(map(str, vector)) + "]"
```

Reject a stored hash with different sentence text. Call OpenAI only for missing unique hashes. Validate response indexes and dimensions. Retry transient failures with bounded exponential backoff for at most five attempts. Store only a safe error code.

- [ ] **Step 4: Implement atomic data and progress writes**

Use stable document IDs from `source:external_id`, existing deterministic chunk IDs, and batch `COPY` or `executemany()` for documents, chunks, and sentences. Compare content hashes before accepting conflicts. Cache rows, searchable rows, and progress must commit together:

```python
with conn.transaction():
    embeddings = embed_missing(conn, run.id, client, model, unique_sentences)
    report = write_document_batch(conn, run, records, identities, embeddings)
    save_progress(conn, run.id, source, report)
```

- [ ] **Step 5: Run focused import and ACL tests**

Run: `TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/full_redwood_import_test PYTHONPATH=.:api/src /Users/mac/workspace/knowledge-browser/api/.venv/bin/pytest -c api/pyproject.toml -q api/tests/test_bulk_import.py api/tests/test_bulk_writer.py api/tests/test_embedding_index.py api/tests/test_bootstrap.py api/tests/test_acl.py`

Expected: all selected tests pass without selecting `full_acl`.

- [ ] **Step 6: Commit**

```bash
git add api/src/knowledge_browser/bulk_import.py api/src/knowledge_browser/bulk_writer.py api/src/knowledge_browser/embedding_index.py api/tests/test_bulk_import.py api/tests/test_embedding_index.py
git commit -m "Import Redwood documents in resumable batches"
```

---

### Task 5: Add Docker and Operator Commands

**Files:**
- Modify: `compose.yaml`
- Create: `api/src/knowledge_browser/bulk_cli.py`
- Create: `scripts/redwood_database.sh`
- Create: `api/tests/test_bulk_cli.py`
- Modify: `README.md`

**Interfaces:**
- Produces `python -m knowledge_browser.bulk_cli {validate,reset,run,status,verify}`.
- Produces `./scripts/redwood_database.sh {start,validate,reset,run,status,verify,stop}`.
- Keeps `./run_server.sh` unchanged.

- [ ] **Step 1: Write failing CLI safety tests**

```python
def test_reset_validates_before_reset(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(bulk_cli, "validate_streaming_dataset", lambda _path: (_ for _ in ()).throw(ValueError("bad manifest")))
    monkeypatch.setattr(bulk_cli, "reset_redwood_database", lambda *_args: calls.append("reset"))
    assert bulk_cli.main(["reset", "--data", str(tmp_path), "--yes"]) == 1
    assert calls == []

def test_cli_hides_exception_details(monkeypatch, capsys):
    monkeypatch.setattr(bulk_cli, "run_import", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("secret text")))
    assert bulk_cli.main(["run", "--data", "/safe/data"]) == 1
    assert capsys.readouterr().err == "Redwood import failed; run status for safe details.\n"
```

- [ ] **Step 2: Run tests and verify red**

Run: `PYTHONPATH=.:api/src /Users/mac/workspace/knowledge-browser/api/.venv/bin/pytest -c api/pyproject.toml -q api/tests/test_bulk_cli.py`

Expected: the CLI module does not exist.

- [ ] **Step 3: Add the opt-in Compose profile**

```yaml
  redwood-db:
    profiles: ["redwood"]
    image: pgvector/pgvector:pg17
    container_name: knowledge-redwood-db
    environment:
      POSTGRES_DB: knowledge_redwood
      POSTGRES_PASSWORD: ${REDWOOD_POSTGRES_PASSWORD:-postgres}
      POSTGRES_USER: ${REDWOOD_POSTGRES_USER:-postgres}
    ports:
      - "127.0.0.1:${REDWOOD_POSTGRES_PORT:-5433}:5432"
    volumes:
      - knowledge_redwood_data:/var/lib/postgresql/data
```

Declare `knowledge_redwood_data` separately. Normal Compose startup must not start this profile.

- [ ] **Step 4: Implement CLI and wrapper**

Use `argparse`. `reset` requires `--yes`; `status` never requires an API key; `run` requires a key only for uncached sentences. The wrapper loads `.env`, then overrides `DATABASE_URL` with the Redwood host, port, and exact database name. Print source, next line, documents, sentences, elapsed time, and provider calls once per committed batch. Never print document text or exception strings.

- [ ] **Step 5: Run CLI and Compose checks**

Run: `PYTHONPATH=.:api/src /Users/mac/workspace/knowledge-browser/api/.venv/bin/pytest -c api/pyproject.toml -q api/tests/test_bulk_cli.py`

Run: `docker compose config --quiet && docker compose --profile redwood config --quiet`

Expected: tests and both Compose checks pass.

- [ ] **Step 6: Commit**

```bash
git add compose.yaml api/src/knowledge_browser/bulk_cli.py scripts/redwood_database.sh api/tests/test_bulk_cli.py README.md
git commit -m "Add full Redwood import commands"
```

---

### Task 6: Finalize Indexes and Verify Quality

**Files:**
- Create: `api/src/knowledge_browser/bulk_verify.py`
- Create: `api/tests/test_bulk_verify.py`
- Modify: `api/src/knowledge_browser/bulk_import.py`
- Modify: `api/src/knowledge_browser/bulk_cli.py`
- Modify: `README.md`

**Interfaces:**
- Produces `finalize_indexes(conn) -> None`.
- Produces `VerificationReport(compatible, counts, sources, missing_embeddings, acl_checks, recall_at_10, mrr, p50_ms, p95_ms)`.
- Produces `verify_redwood(connection_factory, data_dir, embedding_client, profile) -> VerificationReport`.

- [ ] **Step 1: Write failing finalization tests**

```python
def test_finalize_creates_indexes_and_marks_complete(db, run):
    finalize_indexes(db, run.id)
    names = {row[0] for row in db.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public'")}
    assert {"chunks_fts_idx", "sentences_embedding_idx"} <= names
    assert load_run(db, run.id).status == "complete"

def test_verify_blocks_unknown_user(db, tiny_import):
    report = verify_redwood(lambda: db, tiny_import.data_dir, FakeEmbeddingClient(), RELEASED_PROFILE)
    assert report.missing_embeddings == 0
    assert report.acl_checks["unknown_user_results"] == 0
```

- [ ] **Step 2: Run tests and verify red**

Run: `TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/full_redwood_import_test PYTHONPATH=.:api/src /Users/mac/workspace/knowledge-browser/api/.venv/bin/pytest -c api/pyproject.toml -q api/tests/test_bulk_verify.py`

Expected: finalization and verification symbols are missing.

- [ ] **Step 3: Implement restartable index creation**

During explicit reset, omit or drop only Redwood `chunks_fts_idx` and `sentences_embedding_idx`. After all source checkpoints match the manifest, set status `indexing`, create missing indexes, run `ANALYZE users, documents, chunks, sentences`, verify them, and only then set status `complete`.

```sql
CREATE INDEX chunks_fts_idx ON chunks USING gin (fts);
CREATE INDEX sentences_embedding_idx ON sentences USING hnsw (embedding halfvec_cosine_ops);
```

- [ ] **Step 4: Implement exact counts, ACL probes, QA, and latency**

Read `qa.jsonl` unchanged. Evaluate accessible questions with released hybrid retrieval. Compute:

```python
recall_at_10 = found_expected_questions / evaluated_questions
mrr = sum(reciprocal_ranks) / evaluated_questions
p95_ms = statistics.quantiles(latencies_ms, n=100, method="inclusive")[94]
```

Run company, group, direct-user, and random unknown-user probes. Emit JSON to stdout when requested; never create a report file in Git.

- [ ] **Step 5: Run normal test tiers**

Run: `TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/full_redwood_import_test PYTHONPATH=.:api/src /Users/mac/workspace/knowledge-browser/api/.venv/bin/pytest -c api/pyproject.toml -q -m "unit or integration" api/tests`

Run: `npm --prefix web test -- --run && git diff --check`

Expected: all normal selected tests pass. Do not run excluded markers.

- [ ] **Step 6: Commit**

```bash
git add api/src/knowledge_browser/bulk_verify.py api/src/knowledge_browser/bulk_import.py api/src/knowledge_browser/bulk_cli.py api/tests/test_bulk_verify.py README.md
git commit -m "Verify full Redwood indexes"
```

---

### Task 7: Review, Run the Full Import, and Deliver

**Files:**
- Modify after successful full verification: `docs/contracts/full-redwood-import.md`
- Do not create or commit generated reports, caches, Redwood data, or database files.

**Interfaces:**
- Consumes all prior tasks.
- Produces a verified local `knowledge_redwood` database and one squash-merged PR.

- [ ] **Step 1: Run fresh branch verification**

Run the normal unit and integration tiers, search/rag eval excluding nightly, web tests, web build, normal and Redwood Compose validation, and `git diff --check origin/main...HEAD`. Do not run `full_acl`, `full_retrieval`, or `nightly`.

- [ ] **Step 2: Obtain a clean read-only code review**

Review reset guards, transaction/checkpoint atomicity, resume idempotence, ACL mapping, secret-safe errors, provider retries, hash collisions, memory bounds, and index finalization. Fix every Critical or Important finding, rerun focused tests, and request re-review.

- [ ] **Step 3: Validate the full source before reset**

Run: `./scripts/redwood_database.sh validate --data /Users/mac/workspace/knowledge-browser/data/redwood`

Expected: hashes, references, duplicate IDs, and the exact 304,966 artifact count pass with no writes or provider calls.

- [ ] **Step 4: Resolve exact destructive targets and replace only the pilot**

Print and confirm these exact targets: container `knowledge-redwood-db`, volume `knowledge_redwood_data`, host port `5433`, database `knowledge_redwood`. Then run:

```bash
./scripts/redwood_database.sh reset --data /Users/mac/workspace/knowledge-browser/data/redwood --yes
./scripts/redwood_database.sh run --data /Users/mac/workspace/knowledge-browser/data/redwood
```

If interrupted, rerun only `run`; never reset a valid partial run.

- [ ] **Step 5: Verify the full database**

Run: `./scripts/redwood_database.sh status`

Run: `./scripts/redwood_database.sh verify --data /Users/mac/workspace/knowledge-browser/data/redwood --json`

Run: `docker compose exec -T db psql -U postgres -d knowledge_search -Atc "select count(*) from documents;"`

Expected: status `complete`; 304,966 Redwood documents; exact four-source counts; no null embedding; compatibility and focused ACL checks pass; QA and p50/p95 metrics print; normal database remains 1,000 documents.

- [ ] **Step 6: Mark the contract implemented and commit**

Set status to `Implemented and fully imported on 2026-09-02.`, then run:

```bash
git add docs/contracts/full-redwood-import.md
git commit -m "Record verified full Redwood import"
```

- [ ] **Step 7: Push, open one PR, wait for CI, and squash-merge**

Fetch and rebase onto `origin/main`, rerun fresh verification, push `codex/full-redwood-import`, open one focused PR, wait for every required check, and squash-merge. Verify remote `main`, remove the feature worktree and branch, and preserve unrelated local changes in the main checkout.
