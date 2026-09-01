# Portable Database Bootstrap Final Fix Report

## Outcome

All four Important and two Minor findings from the final branch review are
addressed on `codex/portable-db-bootstrap`. No push, pull request, merge, real
Docker/database shell harness, paid OpenAI smoke, or prohibited test tier was
used.

## RED/GREEN evidence

### 1. Manifest-declared counts

- RED: `api/tests/test_dataset.py` added mutations for all seven declared
  counters plus exact-key, boolean, non-integer, and negative-value cases.
  The first focused run reported 12 failures and 5 passes because every invalid
  manifest was accepted.
- GREEN: `validate_manifest()` now requires exactly `artifacts`, `companies`,
  `employees`, `incidents`, `projects`, `qa`, and `teams`; requires non-boolean,
  non-negative integers; hash-verifies the required count-bearing files; and
  compares every count to loaded source records. The dataset suite reports 17
  passes.
- Provider boundary mutation: temporarily bypassing the employee comparison
  made `test_manifest_count_failure_skips_provider` fail at the provider
  sentinel. Restoring the comparison made the focused test pass, proving count
  validation precedes provider creation.

### 2. Credential-safe database setup failures

- RED: the expanded fake-command harness emitted a sentinel password on inline
  failure; the old script exposed it (`config failure exposed the database
  password`). The independent review also found the static readiness timeout
  insufficiently actionable; its new exact-message assertion failed before the
  production message changed.
- GREEN: configuration resolution, readiness, table inspection, and schema
  execution now suppress unsafe child diagnostics and return only static shell
  messages. The readiness timeout points to database settings and service logs.
  Safe bootstrap and compatibility CLI messages remain visible. Sentinel cases
  for all four inline operations pass.

### 3. First server child exit wins

- RED: the API-first fake regression timed out with `api-first runner did not
  exit promptly` under the old sequential `wait`.
- GREEN: a Bash-3.2-compatible watcher records the first child status and the
  existing cleanup trap terminates the sibling. The shell harness passes API
  and web first-exit cases for both status 0 and nonzero statuses 23/37 without
  hanging.

### 4. Configured API port reaches Vite

- RED: the focused Vitest case failed because `createViteConfig` did not exist
  and the proxy remained hard-coded. A second RED cycle showed an unsafe port
  string was accepted.
- GREEN: Vite derives its localhost `/api` proxy from `API_PORT`, defaults to
  8000, and rejects non-numeric/out-of-range ports. Both config tests, all 13 web
  tests, and the production build pass. `run_server.sh` still launches the
  project-local Vite binary directly.

### 5. Named `ParsedDocument` construction

- RED: not applicable to behavior; this was a static maintainability finding
  and intentionally preserves all semantics.
- GREEN: all 13 fields are passed by name, with the existing dataset and import
  coverage remaining green.

### 6. `.env`-only `DATABASE_URL`

- RED mutation: temporarily removing the post-`.env` explicit-URL decision
  caused the new assertion to fail because `compose up -d db` ran before the
  `from-dotenv` operations.
- GREEN: the restored implementation skips Compose and uses `from-dotenv` for
  readiness, table inspection, schema execution, bootstrap, and compatibility.

## Verification commands and results

- `api/.venv/bin/python -m pytest -q -m 'unit and not full_acl and not full_retrieval and not nightly' api/tests`
  - 90 passed, 108 deselected; one pre-existing Starlette deprecation warning.
- `TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/knowledge_browser_final_fix_test api/.venv/bin/python -m pytest -q -m 'integration and not full_acl and not full_retrieval and not nightly' api/tests/test_bootstrap.py api/tests/test_db_compat.py`
  - 38 passed in 7.21 seconds using the dedicated `_test` database and fake
    embeddings; no provider call.
- `bash -n run_server.sh scripts/setup_database.sh scripts/test_setup_database.sh scripts/test_run_server.sh`
  - passed under GNU Bash 3.2.57 on macOS.
- `bash scripts/test_setup_database.sh`
  - `setup database tests passed`.
- `bash scripts/test_run_server.sh`
  - `run server tests passed`.
- `(cd web && npm test -- --run)`
  - 3 files and 13 tests passed.
- `(cd web && npm run build)`
  - TypeScript and Vite production build passed.
- `docker compose config --quiet`
  - passed; no services started.
- `git diff --check` and `git diff --cached --check`
  - passed.
- `test ! -e data/.cache`
  - passed.

All shell harnesses ran sequentially. Every pytest selection explicitly
excluded `full_acl`, `full_retrieval`, and `nightly`. The exhaustive suites and
paid OpenAI smoke were not run.

## Files changed

- `api/src/knowledge_browser/dataset.py`
- `api/tests/test_dataset.py`
- `api/tests/test_bootstrap.py`
- `scripts/setup_database.sh`
- `scripts/test_setup_database.sh`
- `run_server.sh`
- `scripts/test_run_server.sh`
- `web/vite.config.ts`
- `web/vite.config.test.ts`
- This report.

## Self-review

- Re-read the feature contract, design, final reviewer findings, and all changed
  production/test files.
- Confirmed count semantics against the approved source generator: one company,
  JSONL counts for employees/teams/projects/incidents/QA, and the sum of four
  artifact files.
- Confirmed manifest hashes and safe-path checks still run before source counts
  are trusted.
- Confirmed every unsafe inline database boundary discards both credential-
  bearing output channels while the already-sanitized bootstrap and
  compatibility CLIs retain useful messages.
- Confirmed process supervision preserves the first API/web exit status and
  sibling cleanup under Bash 3.2.
- An independent completion review reported no Critical issues. Its one
  Important readiness-message finding was fixed test-first; its Minor provider
  ordering recommendation was added and mutation-checked.

## Concerns and exclusions

- The only observed warning is the existing FastAPI/Starlette `httpx`
  deprecation warning; it is unrelated to this branch.
- `shellcheck` is not installed in the environment; Bash syntax and executable
  behavior were verified directly under the required Bash 3.2 runtime.
- The paid first-run embedding smoke was explicitly excluded and not repeated.
- No remaining Critical, Important, or known behavioral concern was found.

## Commits

- `1df8897` — `fix: address final bootstrap review`
- `docs: record final bootstrap fixes` — this report commit
