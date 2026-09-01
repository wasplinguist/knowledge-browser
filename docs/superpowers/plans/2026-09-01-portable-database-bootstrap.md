# Portable Database Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make \`./run_server.sh\` create and populate a fresh PostgreSQL database, then safely reuse it.

**Architecture:** Commit only the 3.2 MB manifest-verified \`data/company\` snapshot. Python validates, parses, embeds, and imports it transactionally. Bash starts PostgreSQL, creates a missing schema, runs bootstrap and compatibility checks, then starts API and web.

**Tech Stack:** Bash, Docker Compose, PostgreSQL 17/pgvector, Python 3.12, psycopg 3, OpenAI SDK, pytest, React/Vite.

**Spec:** \`docs/superpowers/specs/2026-09-01-portable-database-bootstrap-design.md\`

## Global Constraints

- Work only on \`codex/portable-db-bootstrap\`.
- Copy only old \`data/company\`; never copy \`data/.cache\`, reports, settings, secrets, experiments, or Git history.
- Never replace, truncate, repair, or re-import a populated database.
- Tests use fake 1,536-value embeddings and a database name containing \`_test\`.
- Do not run \`full_acl\`, \`full_retrieval\`, or \`nightly\`.
- Preserve ACL-safe reads, released search behavior, ports 8000/5173, and Ctrl+C cleanup.

---

### Task 1: Canonical source snapshot and strict reader

**Files:**
- Create: \`data/company/**\`
- Create: \`api/src/knowledge_browser/dataset.py\`
- Create: \`api/tests/test_dataset.py\`
- Modify: \`AGENTS.md\`, \`.gitignore\`

**Interfaces:**
- Produces \`ParsedDocument\`, \`Dataset\`, \`validate_manifest(Path)\`, and \`load_dataset(Path)\`.

- [ ] **Step 1: Copy only approved files**

\`\`\`bash
rsync -a --delete /Users/mac/workspace/knowledge-search/data/company/ data/company/
test "$(find data/company -type f | wc -l | tr -d ' ')" = 13
test ! -e data/.cache
\`\`\`

- [ ] **Step 2: Write failing tests**

\`\`\`python
DATASET = Path(__file__).parents[2] / "data" / "company"

def test_canonical_counts():
    manifest = validate_manifest(DATASET)
    dataset = load_dataset(DATASET)
    assert manifest["counts"]["artifacts"] == 1000
    assert len(dataset.users) == 100
    assert len(dataset.documents) == 1000
    assert Counter(item.source for item in dataset.documents) == {
        "confluence": 250, "github": 250, "jira": 250, "slack": 250,
    }

def test_changed_bytes_fail(tmp_path):
    copied = shutil.copytree(DATASET, tmp_path / "company")
    jira = copied / "artifacts" / "jira.jsonl"
    jira.write_bytes(jira.read_bytes() + b" ")
    with pytest.raises(ValueError, match="manifest hash mismatch"):
        validate_manifest(copied)
\`\`\`

Also assert the known restricted artifact maps to \`{"groups": ["Product Platform"]}\` and Jira keeps \`project_alias\` and \`issue_metadata\`.

- [ ] **Step 3: Verify RED**

\`\`\`bash
api/.venv/bin/python -m pytest -q api/tests/test_dataset.py
\`\`\`

Expected: missing \`knowledge_browser.dataset\`.

- [ ] **Step 4: Implement the reader**

\`\`\`python
@dataclass(frozen=True, slots=True)
class ParsedDocument:
    source: str
    kind: str
    external_id: str
    title: str
    body: str
    author: str | None
    url: str | None
    container: str | None
    created_at: str | None
    updated_at: str | None
    acl: dict[str, Any] | None
    raw_payload: dict[str, Any]
    fields: dict[str, list[str]]

@dataclass(frozen=True, slots=True)
class Dataset:
    users: tuple[dict[str, Any], ...]
    groups: tuple[dict[str, Any], ...]
    documents: tuple[ParsedDocument, ...]
\`\`\`

Implement safe manifest paths, required-file and SHA-256 checks, strict JSONL objects, unique IDs, and validation of employee/team/project/artifact/ACL references. Adapt source field and ACL mapping from the approved old \`synthetic_dataset.py\`. Invalid ACL means hidden, never company-visible.

- [ ] **Step 5: Update rules, verify, commit**

Allow only \`data/company\` in \`AGENTS.md\`; keep all other old artifacts and generation deferred.

\`\`\`bash
api/.venv/bin/python -m pytest -q api/tests/test_dataset.py
test ! -e data/.cache
git diff --check
git add AGENTS.md .gitignore data/company api/src/knowledge_browser/dataset.py api/tests/test_dataset.py
git commit -m "feat: add validated company dataset"
\`\`\`

---

### Task 2: Production database schema

**Files:**
- Create: \`db/init/001_schema.sql\`
- Create: \`api/tests/test_bootstrap_schema.py\`
- Modify: \`api/tests/conftest.py\`

**Interfaces:** Produces one schema used by local setup and integration tests.

- [ ] **Step 1: Write failing integration test**

\`\`\`python
SCHEMA = Path(__file__).parents[2] / "db" / "init" / "001_schema.sql"

def test_production_schema_has_required_tables(prepared_test_database):
    assert SCHEMA.is_file()
    with psycopg.connect(prepared_test_database) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        )}
    assert {"users", "groups", "permission_sets", "documents", "chunks",
            "sentences", "search_events", "search_clicks"} <= tables
\`\`\`

- [ ] **Step 2: Verify RED**

\`\`\`bash
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/knowledge_browser_bootstrap_test api/.venv/bin/python -m pytest -q api/tests/test_bootstrap_schema.py
\`\`\`

- [ ] **Step 3: Promote current compatible fixture**

Create production SQL from \`api/tests/fixtures/existing_schema.sql\`. Preserve extensions, ACL constraints, partitions, FTS, GIN/HNSW indexes, \`halfvec(1536)\`, and analytics. Add no DROP/TRUNCATE. Make test setup read production SQL after its test-only reset.

- [ ] **Step 4: Verify and commit**

\`\`\`bash
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/knowledge_browser_bootstrap_test api/.venv/bin/python -m pytest -q api/tests/test_bootstrap_schema.py api/tests/test_db_compat.py api/tests/test_repository.py
git add db/init/001_schema.sql api/tests/conftest.py api/tests/test_bootstrap_schema.py
git commit -m "feat: add bootstrap database schema"
\`\`\`

---

### Task 3: Embedding batching

**Files:**
- Create: \`api/src/knowledge_browser/embedding_index.py\`
- Create: \`api/tests/test_embedding_index.py\`

**Interfaces:** Produces \`sentences\`, \`collect_sentences\`, and \`create_embeddings\`.

- [ ] **Step 1: Write failing test**

\`\`\`python
def test_batches_dedupes_and_uses_provider_indexes():
    calls = []
    class Embeddings:
        def create(self, *, model, input):
            calls.append((model, input))
            return SimpleNamespace(data=[
                SimpleNamespace(index=i, embedding=[float(len(text))] * 1536)
                for i, text in reversed(list(enumerate(input)))
            ])
    result = create_embeddings(SimpleNamespace(embeddings=Embeddings()),
        ["one", "two-two", "one", "three"], "text-embedding-3-small", batch_size=2)
    assert calls == [("text-embedding-3-small", ["one", "two-two"]),
                     ("text-embedding-3-small", ["three"])]
    assert result["one"] == [3.0] * 1536
\`\`\`

Also test sentence order, deduplication, \`issue_metadata\` exclusion, invalid batch size, bad indexes, and bad vector size.

- [ ] **Step 2: Verify RED**

\`\`\`bash
api/.venv/bin/python -m pytest -q api/tests/test_embedding_index.py
\`\`\`

- [ ] **Step 3: Implement**

\`\`\`python
def sentences(text):
    return [part.strip() for part in re.findall(r"[^.!?]+[.!?]?", text) if part.strip()]

def collect_sentences(documents):
    values = []
    for document in documents:
        for field, texts in document.fields.items():
            if field == "issue_metadata":
                continue
            for text in filter(None, texts):
                values.extend(sentences(text))
    return list(dict.fromkeys(values))

def create_embeddings(client, texts, model, *, batch_size=100):
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    unique, result = list(dict.fromkeys(texts)), {}
    for start in range(0, len(unique), batch_size):
        batch = unique[start:start + batch_size]
        response = client.embeddings.create(model=model, input=batch)
        by_index = {item.index: item.embedding for item in response.data}
        if set(by_index) != set(range(len(batch))):
            raise ValueError("embedding provider returned invalid indexes")
        if any(len(vector) != 1536 for vector in by_index.values()):
            raise ValueError("embedding provider returned invalid dimensions")
        result.update({text: by_index[index] for index, text in enumerate(batch)})
    return result
\`\`\`

- [ ] **Step 4: Verify and commit**

\`\`\`bash
api/.venv/bin/python -m pytest -q api/tests/test_embedding_index.py
git add api/src/knowledge_browser/embedding_index.py api/tests/test_embedding_index.py
git commit -m "feat: batch canonical embeddings"
\`\`\`

---

### Task 4: Transactional importer

**Files:**
- Create: \`api/src/knowledge_browser/importer.py\`
- Create: \`api/tests/test_importer.py\`

**Interfaces:** Produces \`ImportReport\` and \`import_dataset(conn, dataset, embeddings, model)\`.

- [ ] **Step 1: Write failing tests**

\`\`\`python
DATA = Path(__file__).parents[2] / "data" / "company"

def test_imports_expected_counts(db):
    db.execute("TRUNCATE documents, users, groups, permission_sets CASCADE")
    dataset = load_dataset(DATA)
    vectors = {text: [0.0] * 1536 for text in collect_sentences(dataset.documents)}
    report = import_dataset(db, dataset, vectors, model="text-embedding-3-small")
    assert (report.users, report.documents, report.chunks, report.sentences) == (100, 1000, 13145, 16520)

def test_missing_embedding_rolls_back(db):
    db.execute("TRUNCATE documents, users, groups, permission_sets CASCADE")
    with pytest.raises(ValueError, match="missing embedding"):
        import_dataset(db, load_dataset(DATA), {}, model="text-embedding-3-small")
    assert db.execute("SELECT count(*) FROM documents").fetchone() == (0,)
\`\`\`

Add focused checks for one company, team, direct, unrelated, and unknown user. Do not loop over all users/queries.

- [ ] **Step 2: Verify RED**

\`\`\`bash
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/knowledge_browser_bootstrap_test api/.venv/bin/python -m pytest -q api/tests/test_importer.py
\`\`\`

- [ ] **Step 3: Implement**

\`\`\`python
@dataclass(frozen=True, slots=True)
class ImportReport:
    users: int
    documents: int
    chunks: int
    sentences: int

def import_dataset(conn, dataset, embeddings, *, model):
    with conn.transaction():
        return _import_dataset(conn, dataset, embeddings, model=model)
\`\`\`

Insert in order: users; groups/memberships; permission sets keyed by SHA-256 of sorted ACL JSON; permission links; root documents; chunks; sentences. Use document UUID as both ID/root ID. Chunk ID is \`external_id:field:index\`. Missing ACL is restricted. Parameterize values. Do not skip invalid records. A missing embedding raises and rolls back. Return final counts.

- [ ] **Step 4: Verify focused tests and commit**

\`\`\`bash
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/knowledge_browser_bootstrap_test api/.venv/bin/python -m pytest -q api/tests/test_importer.py api/tests/test_native_acl.py api/tests/test_search.py
git add api/src/knowledge_browser/importer.py api/tests/test_importer.py
git commit -m "feat: import canonical company data"
\`\`\`

Expected: PASS; no \`full_acl\`.

---

### Task 5: Idempotent bootstrap CLI

**Files:**
- Create: \`api/src/knowledge_browser/bootstrap.py\`
- Create: \`api/tests/test_bootstrap.py\`

**Interfaces:** Produces \`BootstrapError\`, \`BootstrapResult\`, \`bootstrap_database\`, and module CLI.

- [ ] **Step 1: Write failing state tests**

\`\`\`python
def test_populated_database_skips_provider():
    called = False
    def client_factory():
        nonlocal called
        called = True
        raise AssertionError("provider must not run")
    result = bootstrap_database(populated_connection_factory, DATA, client_factory)
    assert result.imported is False
    assert called is False

def test_partial_database_is_refused():
    with pytest.raises(BootstrapError, match="partially initialized"):
        bootstrap_database(partial_connection_factory, DATA,
            lambda: (_ for _ in ()).throw(AssertionError("provider must not run")))
\`\`\`

Add empty import, incompatible populated DB, provider error, and post-import compatibility cases.

- [ ] **Step 2: Verify RED**

\`\`\`bash
api/.venv/bin/python -m pytest -q api/tests/test_bootstrap.py
\`\`\`

- [ ] **Step 3: Implement state machine**

\`\`\`python
def bootstrap_database(connection_factory, data_dir, client_factory):
    with connection_factory() as conn:
        if conn.execute("SELECT count(*) FROM documents").fetchone()[0]:
            if not check_compatibility(conn).compatible:
                raise BootstrapError("existing database is incompatible")
            return BootstrapResult(False, None)
        if conn.execute(PARTIAL_COUNT_SQL).fetchone()[0]:
            raise BootstrapError("database is partially initialized")
        dataset = load_dataset(data_dir)
        model = load_profile(PROFILE_PATH).embedding_model
        vectors = create_embeddings(client_factory(), collect_sentences(dataset.documents), model)
        with conn.transaction():
            report = import_dataset(conn, dataset, vectors, model=model)
            if not check_compatibility(conn).compatible:
                raise BootstrapError("imported database failed compatibility check")
        return BootstrapResult(True, report)
\`\`\`

CLI checks \`OPENAI_API_KEY\` only for empty setup, creates \`OpenAI()\` only then, prints safe counts or “database already initialized,” and never prints exception repr, URLs, keys, payloads, or content.

- [ ] **Step 4: Verify and commit**

\`\`\`bash
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/knowledge_browser_bootstrap_test api/.venv/bin/python -m pytest -q api/tests/test_bootstrap.py api/tests/test_db_compat.py api/tests/test_health.py
git add api/src/knowledge_browser/bootstrap.py api/tests/test_bootstrap.py
git commit -m "feat: bootstrap empty databases"
\`\`\`

---

### Task 6: Database setup and server runner

**Files:**
- Create: \`.env.example\`
- Create: \`scripts/setup_database.sh\`, \`scripts/test_setup_database.sh\`
- Create: \`run_server.sh\`, \`scripts/test_run_server.sh\`
- Modify: \`README.md\`

**Interfaces:** Produces standalone DB setup and single command \`./run_server.sh\`.

- [ ] **Step 1: Write failing shell tests**

Use temporary fake Docker/Python commands. Assert empty order: Compose up, readiness, schema count, schema stdin, bootstrap, compatibility. Add complete-schema skip, partial-schema refusal, readiness timeout, and bootstrap failure. Runner test uses safe random ports and asserts env export, old listener stop, both URLs, and TERM cleanup.

\`\`\`bash
bash scripts/test_setup_database.sh
bash scripts/test_run_server.sh
\`\`\`

Expected: missing production scripts.

- [ ] **Step 2: Implement setup**

Use \`set -euo pipefail\`, load/export \`.env\`, start Compose DB, poll readiness 30 times. Count 11 required tables. Count 0 applies schema with \`ON_ERROR_STOP=1\`; count 11 skips; any other count stops as partial. Run bootstrap then compatibility. Keep \`DATABASE_URL\` priority.

- [ ] **Step 3: Implement runner**

\`\`\`bash
#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd "$(dirname "$0")" && pwd)"
set -a
source "$project_root/.env"
set +a
"$project_root/scripts/setup_database.sh"
\`\`\`

Then free ports with \`lsof\`, start uvicorn and direct Vite (\`exec ./node_modules/.bin/vite --host 127.0.0.1\`), trap EXIT/INT/TERM, and wait. Add safe \`.env.example\` with PostgreSQL defaults, blank key, and answer model.

- [ ] **Step 4: Update README, verify, commit**

\`\`\`bash
chmod +x run_server.sh scripts/setup_database.sh scripts/test_setup_database.sh scripts/test_run_server.sh
bash -n run_server.sh scripts/setup_database.sh scripts/test_setup_database.sh scripts/test_run_server.sh
bash scripts/test_setup_database.sh
bash scripts/test_run_server.sh
docker compose config --quiet
git diff --check
git add .env.example README.md run_server.sh scripts/setup_database.sh scripts/test_setup_database.sh scripts/test_run_server.sh
git commit -m "feat: initialize database before server startup"
\`\`\`

---

### Task 7: Focused verification and delivery

- [ ] **Step 1: Allowed API checks**

\`\`\`bash
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/knowledge_browser_bootstrap_test api/.venv/bin/python -m pytest -q -m "(unit or integration) and not full_acl and not full_retrieval and not nightly" api/tests
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/knowledge_browser_bootstrap_test api/.venv/bin/python -m pytest -q -m "(search_eval or rag_eval) and not full_acl and not full_retrieval and not nightly" api/tests
\`\`\`

- [ ] **Step 2: Web/repository checks**

\`\`\`bash
(cd web && npm test -- --run)
(cd web && npm run build)
bash scripts/test_setup_database.sh
bash scripts/test_run_server.sh
docker compose config --quiet
api/.venv/bin/python -m pytest --collect-only -q --strict-markers api/tests
git diff --check
test ! -e data/.cache
\`\`\`

- [ ] **Step 3: Disposable first-run smoke**

\`\`\`bash
COMPOSE_PROJECT_NAME=knowledge_browser_bootstrap_smoke POSTGRES_DB=knowledge_browser_bootstrap_smoke_test POSTGRES_PORT=5549 ./scripts/setup_database.sh
\`\`\`

Expected: 1,000 documents, 13,145 chunks, 16,520 embedded sentences. This is the only API-credit check. Clean only this named Compose project/volume.

- [ ] **Step 4: Mark contract implemented and commit**

\`\`\`bash
git add docs/contracts/portable-db-bootstrap.md
git commit -m "docs: mark database bootstrap implemented"
\`\`\`

- [ ] **Step 5: Review, push, open one PR**

\`\`\`bash
git status --short
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
git push -u origin codex/portable-db-bootstrap
gh pr create --base main --head codex/portable-db-bootstrap --title "Add portable first-run database bootstrap" --body "Implements the approved portable database bootstrap contract. Excludes full ACL, full retrieval, and nightly checks."
\`\`\`

Do not merge before review.
