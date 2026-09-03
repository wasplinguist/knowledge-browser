# Knowledge Browser

Knowledge Browser is a local company-knowledge search product for Slack, Jira,
Confluence, and GitHub content. It combines ACL-safe hybrid retrieval with
grounded AI answers and source-aware document views.

The repository includes the API, web app, PostgreSQL development service,
Redwood dataset, released search profile, evaluation queries, and release
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
4. validates and resumably imports the committed `data/redwood/` dataset;
5. creates embeddings, builds search indexes, and checks compatibility;
6. starts the FastAPI and React/Vite development servers.

The first import creates 7,245 users, 13,214 documents, 398,919 chunks, and
1,062,078 sentences. Completed batches are checkpointed, so an interrupted
setup continues instead of starting over. Later runs verify and reuse the
populated database.

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

### Isolated Redwood database

The committed `data/redwood/` dataset can also be imported into a separate
PostgreSQL database for evaluation and operator checks. This workflow never
changes the normal
`knowledge_search` database, and normal `docker compose up` does not start the
Redwood service.

```bash
./scripts/redwood_database.sh start
./scripts/redwood_database.sh validate --data /path/to/redwood
./scripts/redwood_database.sh reset --data /path/to/redwood --yes
./scripts/redwood_database.sh run --data /path/to/redwood
./scripts/redwood_database.sh status
./scripts/redwood_database.sh verify --data /path/to/redwood --json
./scripts/redwood_database.sh stop
```

The `run` command uses a bounded 200-document work window, eight embedding
requests at a time, up to 512 inputs and 50,000 estimated tokens per request.
Use the `--work-window-size`, `--embedding-concurrency`,
`--embedding-max-inputs`, `--embedding-max-tokens`, and
`--embedding-*-timeout` flags to lower these safe limits. Progress shows cache
hits, provider requests, retries, sentence throughput, and estimated time
remaining. `status` reports `running`, `stalled`, `failed`, `indexing`, or
`complete`.

Before a full import, run the deterministic 200-document performance gate:

```bash
PYTHONPATH=api/src api/.venv/bin/python -m knowledge_browser.bulk_benchmark \
  --data /path/to/redwood --source slack --start-line 801 --documents 200
```

It exits with an error below 5x legacy throughput, at 2 GB memory, or when the
old and new sentence/vector results differ. It prints one JSON object and does
not write a report file. The normal import tests separately prove that provider
calls leave no transaction open and that a stopped run reuses cached work.

`reset` first validates the complete dataset and then requires `--yes`. It
refuses every database name except `knowledge_redwood`. The import saves each
completed batch, so running `run` again continues from the last saved line.
Only uncached sentences need the configured OpenAI API key. The large text and
vector indexes are built after all batches load. Status stays `indexing` until
both indexes are valid, the tables are analyzed, and the run is complete.

`verify` reads `qa.jsonl` without changing it and uses the released hybrid
search profile. Its ACL probes also use that real search path for company,
group, unauthorized, and unknown-user checks. When the validated source data
contains direct-user ACLs, it also runs strict authorized and unauthorized
direct-user searches. When there are none, the report shows
`direct_user_status: not_applicable` with null direct results. In that case,
`direct_user_database_links` must be zero; any unexpected database link is
reported and makes verification fail. It checks exact document and source
counts, embeddings, Recall@10, MRR, and local search
p50/p95 latency. Semantic retrieval uses the finalized partition HNSW indexes
before bounded result deduplication. Verification fails when p95 is over the
two-second local target. It needs the OpenAI API key for query embeddings.
`--json` prints the same safe aggregate report to standard output; no report
file is created in the repository.

If a manually created container already uses the name
`knowledge-redwood-db`, `start` stops safely. Remove that exact container only
after confirming it is the old Redwood pilot, then run `start` again. The
`reset` and `run` commands also refuse to write until that Compose-managed
container exists.

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

## Project architecture

Knowledge Browser runs the web app and API directly on the host. Docker Compose
runs PostgreSQL with pgvector. The first startup validates and resumably
imports the committed Redwood dataset; later startups verify and reuse the populated
database without resetting it.

![Knowledge Browser project architecture](docs/images/project-architecture.svg)

| Area | Primary paths | Responsibility |
| --- | --- | --- |
| Local runtime | `run_server.sh`, `web/`, `api/` | Start React/Vite and FastAPI, serve search, documents, answers, and event capture |
| Data and index | `data/redwood/`, `db/init/`, `scripts/setup_database.sh` | Validate the manifest, checkpoint import batches, import ACLs and artifacts, and build text and vector indexes |
| Search configuration | `search/profiles/` | Keep released and challenger retrieval settings versioned and reviewable |
| Evaluation | `eval/`, `scripts/run_eval_loop.py` | Compare profiles on controlled queries and write review artifacts outside Git |

The API package lives in `api/src/knowledge_browser/`. PostgreSQL stores users,
groups, permissions, root and child documents, full-text chunks, sentence
vectors, and search events. OpenAI provides first-run sentence embeddings,
query embeddings, and grounded answer generation; keyword retrieval remains
available if a query embedding fails after setup.

The main API routes are:

- `GET /api/health`
- `GET /api/demo-users`
- `GET /api/search`
- `POST /api/answer`
- `GET /api/documents/{source}/{external_id}`
- `POST /api/search-events/{search_id}/click`

## Search architecture

One profile-controlled hybrid pipeline powers the result list and the initial
evidence search for grounded answers. Permission checks happen in SQL before a
document can become a candidate, not after ranking.

![Knowledge Browser hybrid retrieval and ranking pipeline](docs/images/search-retrieval-pipeline.svg)

1. The API resolves the selected demo identity, validates the optional source,
   and normalizes the query with the active profile. Profiles can apply
   whole-term project aliases without rewriting Jira issue keys or larger
   words.
2. Keyword and semantic retrieval run independently. PostgreSQL full-text
   search favors exact identifiers and term overlap; pgvector finds the nearest
   indexed sentence by meaning. Both paths require access to the matched child
   and its canonical root.
3. Reciprocal rank fusion combines rank positions rather than incompatible raw
   scores. Profile-weighted exact Jira-key, freshness, source-authority, and
   primary-project signals can reorder candidates already found by retrieval.
4. Matches are grouped by canonical root, keeping the highest-ranked matching
   child excerpt while returning each root only once.
5. Search returns ranked snippets and source facets. The answer workflow treats
   snippets as leads, opens full allowed chunks, and accepts citations only from
   evidence opened during that request.

The runtime default is `search/profiles/released.json`. Files under
`search/profiles/candidates/` do not change product behavior by themselves;
promotion requires fresh quality evidence, exhaustive ACL verification, and
human approval.

## Dataset structure

Knowledge Browser ships one committed dataset under `data/redwood/`. Startup
validates its complete manifest, builds identity and access metadata from the
organization records, and indexes the Slack, Jira, GitHub, and Confluence
renderings under `data/redwood/artifacts/`.

![Knowledge Browser dataset structure](docs/images/dataset-structure.svg)

- `employees.jsonl`, `teams.jsonl`, `projects.jsonl`, and `acl.jsonl` produce
  user, group, project-alias, and access metadata during import.
- `world.json`, `events.jsonl`, and `qa.jsonl` are manifest-verified
  organization, event, and expected-answer source records.
  They do not become searchable documents or direct evaluation inputs.
- `manifest.json` records the dataset version, seed, counts, and SHA-256 digest
  of every listed dataset file.
- `eval/fixture_queries.json` is the eleven-query pull-request smoke set;
  `eval/redwood_queries.json` is the complete golden set. Neither is saved run
  output.

The repository does not contain a dataset generator or a saved evaluation-run
directory. The eval-driven loop writes each run to a new output directory
outside Git, such as `/tmp/knowledge-browser-runs/<id>`.

## Golden set and evaluation

The committed benchmark covers Redwood knowledge rendered across Slack, Jira,
GitHub, and Confluence, including duplicates, stale claims, conflicts,
distractors, and access restrictions. Expected evidence is defined
independently from search output.

![Golden set construction and evaluation](docs/images/golden-set-evaluation.svg)

The dataset contains 7,245 employees in 12 teams, 12 projects, and 13,214
artifacts: 1,904 Confluence, 3,825 GitHub, 3,303 Jira, and 4,182 Slack records.
`data/redwood/manifest.json` fingerprints every listed file. The evaluation
definitions are separated by runtime cost:

| File | Purpose |
| --- | --- |
| `eval/fixture_queries.json` | Eleven small, hand-checkable queries; one whole-corpus sweep per distinct ACL shape |
| `eval/redwood_queries.json` | The complete 298-question retrieval benchmark |

| Question family | Questions | What it evaluates |
| --- | ---: | --- |
| **Lexical / known item** | 83 | Exact identifiers, names, configuration values, and keywords |
| **Semantic** | 135 | The same meaning expressed with different wording |
| **Multi-hop** | 22 | Evidence combined across documents or source systems |
| **Temporal** | 2 | Current truth and ordering across changing claims |
| **Negative / not found** | 44 | Returning no unsupported or unauthorized evidence |
| **Answer-only** | 12 | Answer expectations whose removed evidence is not retrieval-scored |
| **Total** | **298** | |

Retrieval reports MRR@10 for the first relevant result, nDCG@10 for graded
ordering, Recall@10 for expected-evidence coverage, and forbidden-result leaks.
Released-versus-challenger comparisons also record per-query wins, losses, and
ties plus latency.

The released profile produced the following Redwood baseline on 2026-09-02.
Ranking means include only the 244 questions with relevance labels; all 298
questions still participate in latency and forbidden-result checks.

| Question family | Questions | Ranking-scored | MRR@10 | nDCG@10 | Recall@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Lexical / known item | 83 | 83 | 0.636 | 0.689 | 0.855 |
| Semantic | 135 | 135 | 0.465 | 0.477 | 0.596 |
| Multi-hop | 22 | 22 | 0.815 | 0.712 | 0.785 |
| Temporal | 2 | 2 | 1.000 | 1.000 | 1.000 |
| Answer-only | 12 | 2 | 0.250 | 0.095 | 0.083 |
| Negative / not found | 44 | 0 | N/A | N/A | N/A |
| **Overall** | **298** | **244** | **0.557** | **0.571** | **0.700** |

The run returned zero forbidden-result leaks with 78 ms mean, 68 ms p50, and
124 ms p95 search latency. Generated per-query output remains outside Git.

Fast pull-request checks use four fixture queries and a deterministic ACL sample.
The `full_retrieval` gate runs all 298 questions against the populated database.
The `full_acl` gate requires zero canonical-root and matched-child leaks. Full
retrieval may run nightly; full ACL is an explicit manual release gate. Neither
is an ordinary pull-request check.

These controlled results compare Knowledge Browser profiles on the committed
Redwood corpus. They do not by themselves prove superiority over native Slack,
Jira, GitHub, or Confluence search.

## Eval-driven development loop

Search changes begin with fresh behavior evidence, not a speculative ranking
tweak. One observed problem becomes one intent-audited hypothesis, one new
challenger, and one new comparison.

![Eval-driven development lifecycle](docs/images/eval-driven-development-loop.svg)

```text
Behavior → Insight → Hypothesis → Intent audit → Challenger → New eval → Decision
```

Generate a fresh read-only behavior report outside every Git worktree:

```bash
PYTHONPATH=api/src api/.venv/bin/python scripts/run_eval_loop.py analyze \
  --days 7 \
  --output-dir /tmp/knowledge-browser-behavior
```

After the contract, tests, challenger, and experiment manifest are committed in
a clean worktree, run a fresh comparison:

```bash
PYTHONPATH=api/src api/.venv/bin/python scripts/run_eval_loop.py evaluate \
  --experiment eval/experiments/<id>/experiment.json \
  --output-dir /tmp/knowledge-browser-runs/<id>
```

The runner rejects stale or empty evidence, an unchanged challenger, a
non-`ALIGNED` intent audit, incomplete embeddings, a dirty worktree, mismatched
input hashes, or an output directory that already exists. Baseline, challenger,
and fast ACL checks share one read-only repeatable-read database snapshot.

A passing development comparison can only recommend the separate release gate.
The exhaustive full ACL check and human approval are still required before a
profile is promoted. No evaluation command edits
`search/profiles/released.json` automatically.

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

The exhaustive `full_retrieval` group is a manual or nightly gate. `full_acl`
is a separate manual release gate.

## Project rules

- Product intent: `docs/PRODUCT_INTENT.md`
- Feature contract template: `docs/contracts/FEATURE_CONTRACT_TEMPLATE.md`
- Repository workflow: `AGENTS.md`
