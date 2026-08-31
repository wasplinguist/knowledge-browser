# Runtime Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the smallest runnable API, web application, PostgreSQL development service, and CI checks needed by later Knowledge Browser features.

**Architecture:** Keep the API and web application independent. FastAPI exposes only a health route; React renders only a product shell. PostgreSQL is available for the next schema feature but this PR creates no tables and makes no database connection.

**Tech Stack:** Python 3.12, FastAPI, pytest, HTTPX2, React 19.2.8, TypeScript 7.0.2, Vite 8.2.2, Vitest 4.1.11, Node.js 22.22.2 or newer, PostgreSQL 17 with pgvector, Docker Compose, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-31-clean-product-migration-design.md`

## Global Constraints

- One feature contract, one branch, one worktree, and one pull request.
- Squash-merge the pull request into `main` after verification.
- Do not copy old Git history, product code, datasets, reports, caches, or local settings.
- The old `knowledge-search` repository is read-only reference material.
- No search, RAG, source ingestion, database schema, ACL implementation, synthetic data, or evaluation behavior belongs in this PR.
- The API health route must not require PostgreSQL or external network access.
- Tests must not call OpenAI or any paid service.
- Keep the dependency set minimal; no formatter, linter, CSS framework, state library, or API client library is added.

## File Map

```text
docs/superpowers/plans/2026-08-31-runtime-foundation.md implementation plan
.github/workflows/ci.yml              pull-request verification
compose.yaml                          local PostgreSQL/pgvector service
README.md                             exact setup and verification commands
docs/contracts/runtime-foundation.md approved behavior and non-goals
api/pyproject.toml                    API package and test configuration
api/src/knowledge_browser/__init__.py package marker
api/src/knowledge_browser/main.py     FastAPI app and health route
api/tests/test_health.py              API acceptance test
web/package.json                      web scripts and exact dependency versions
web/package-lock.json                 reproducible npm dependency graph
web/tsconfig.json                     strict browser TypeScript settings
web/vite.config.ts                    React, test environment, and API proxy
web/index.html                        browser entry document
web/src/main.tsx                      React mount point
web/src/App.tsx                       minimal product shell
web/src/App.test.tsx                  shell acceptance test
web/src/test-setup.ts                 jest-dom assertions for Vitest
web/src/styles.css                    minimal readable layout
web/src/vite-env.d.ts                 Vite client type declarations
```

---

### Task 1: Approve the runtime foundation contract

**Files:**
- Create: `docs/contracts/runtime-foundation.md`

**Interfaces:**
- Consumes: repository rules in `AGENTS.md` and product guardrails in `docs/PRODUCT_INTENT.md`.
- Produces: the exact scope and acceptance criteria used by Tasks 2–6.

- [ ] **Step 1: Create the contract**

Create `docs/contracts/runtime-foundation.md` with exactly this content:

```markdown
# Feature contract: runtime foundation

## Status

Approved

## User outcome

A developer can clone the repository, run a health-only API, open a minimal web
application, and start PostgreSQL with pgvector. Pull requests automatically
check these foundations.

## Evidence

Every later feature needs a reproducible Python, Node.js, browser, database, and
CI base. The new repository currently contains contracts only.

## Scope

- Installable Python package with a FastAPI application.
- `GET /api/health` returning HTTP 200 and `{"status": "ok"}`.
- Minimal React page showing `Knowledge Browser` and `Foundation ready`.
- PostgreSQL 17 with pgvector through Docker Compose.
- CI for API tests, web tests, web build, and Compose validation.
- README commands matching CI.

## Non-goals

- Database tables or migrations.
- Identity, ACL, ingestion, search, RAG, synthetic data, or evaluation.
- Production deployment, authentication, or real connectors.
- UI design beyond a readable shell.

## Dependencies

Only bootstrap commit `89daa76` on `main`.

## Interface and data contract

`GET /api/health` has no request body, query parameters, authentication, or
database dependency. Its JSON response is exactly `{"status": "ok"}`.

The web shell has no API request. Vite proxies future `/api` development calls
to `http://127.0.0.1:8000`.

Docker Compose exposes PostgreSQL on `${POSTGRES_PORT:-5432}` with database
`${POSTGRES_DB:-knowledge_browser}` and local demo credentials. No schema is
mounted in this feature.

## Safety invariants

- No secret is committed.
- The health response contains no environment or database details.
- No external network call occurs during tests.
- No endpoint reads or returns company content.

## Quality and performance

The health test must finish without PostgreSQL. The complete API unit test,
web test, and web build should each finish in under one minute on a normal
development machine.

## Acceptance criteria

- `GET /api/health` returns HTTP 200 and exactly `{"status": "ok"}`.
- The web shell renders both required text labels.
- `docker compose config --quiet` succeeds.
- API tests, web tests, and web production build succeed in CI.
- The repository contains no search, RAG, data, schema, or ACL implementation.

## Verification

- `api/.venv/bin/python -m pytest -q api/tests`
- `npm test -- --run` from `web/`
- `npm run build` from `web/`
- `docker compose config --quiet`
- `git diff --check`

## Source reference

Only dependency choices and general directory names were inspected at final
old-repository commit `8782676`. No old product code or Git history is imported.
```

- [ ] **Step 2: Check the contract for forbidden scope**

Run:

```bash
rg -n "search implementation|RAG implementation|CREATE TABLE|OpenAI" docs/contracts/runtime-foundation.md
```

Expected: no output and exit code 1.

- [ ] **Step 3: Commit the contract**

```bash
git add docs/contracts/runtime-foundation.md
git commit -m "docs: define runtime foundation contract"
```

---

### Task 2: Add the health-only FastAPI package

**Files:**
- Create: `api/pyproject.toml`
- Create: `api/src/knowledge_browser/__init__.py`
- Create: `api/src/knowledge_browser/main.py`
- Create: `api/tests/test_health.py`

**Interfaces:**
- Consumes: the exact health response from the runtime contract.
- Produces: `knowledge_browser.main:create_app() -> FastAPI` and `knowledge_browser.main:app` for Uvicorn.

- [ ] **Step 1: Create package configuration**

Create `api/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "knowledge-browser-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi>=0.115,<1", "uvicorn>=0.30,<1"]

[project.optional-dependencies]
dev = ["httpx2>=2,<3", "pytest>=8,<10"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.setuptools.packages.find]
where = ["src"]
```

Create empty `api/src/knowledge_browser/__init__.py`.

- [ ] **Step 2: Write the failing health test**

Create `api/tests/test_health.py`:

```python
from fastapi.testclient import TestClient

from knowledge_browser.main import create_app


def test_health_is_small_and_has_no_database_dependency():
    response = TestClient(create_app()).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 3: Create the virtual environment and verify RED**

Run:

```bash
python3.12 -m venv api/.venv
api/.venv/bin/python -m pip install -e './api[dev]'
api/.venv/bin/python -m pytest -q api/tests/test_health.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'knowledge_browser.main'`.

- [ ] **Step 4: Implement the minimal application**

Create `api/src/knowledge_browser/main.py`:

```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Knowledge Browser")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 5: Verify GREEN**

Run:

```bash
api/.venv/bin/python -m pytest -q api/tests/test_health.py
```

Expected: `1 passed`.

- [ ] **Step 6: Commit the API foundation**

```bash
git add api/pyproject.toml api/src/knowledge_browser api/tests/test_health.py
git commit -m "feat: add health-only API foundation"
```

---

### Task 3: Add the minimal React shell

**Files:**
- Create: `web/package.json`
- Create: `web/package-lock.json`
- Create: `web/tsconfig.json`
- Create: `web/vite.config.ts`
- Create: `web/index.html`
- Create: `web/src/main.tsx`
- Create: `web/src/App.tsx`
- Create: `web/src/App.test.tsx`
- Create: `web/src/test-setup.ts`
- Create: `web/src/styles.css`

**Interfaces:**
- Consumes: the visible labels from the runtime contract.
- Produces: a Vite application mounted at `#root` and a future `/api` development proxy.

- [ ] **Step 1: Create the exact web package configuration**

Create `web/package.json`:

```json
{
  "name": "knowledge-browser-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest"
  },
  "dependencies": {
    "react": "19.2.8",
    "react-dom": "19.2.8"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "7.0.1",
    "@testing-library/react": "16.3.3",
    "@testing-library/user-event": "14.6.6",
    "@types/react": "19.2.18",
    "@types/react-dom": "19.2.5",
    "@vitejs/plugin-react": "6.1.1",
    "jsdom": "30.0.1",
    "typescript": "7.0.2",
    "vite": "8.2.2",
    "vitest": "4.1.11"
  }
}
```

Run `npm install` from `web/` to generate `web/package-lock.json`.

- [ ] **Step 2: Add TypeScript, Vite, and HTML configuration**

Create `web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["DOM", "DOM.Iterable", "ES2022"],
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx"
  },
  "include": ["src"]
}
```

Create `web/vite.config.ts`:

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  test: { environment: 'jsdom', setupFiles: ['./src/test-setup.ts'] },
})
```

Create `web/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Knowledge Browser</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 3: Write the failing shell test**

Create `web/src/test-setup.ts`:

```typescript
import '@testing-library/jest-dom/vitest'
```

Create `web/src/App.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'

describe('App', () => {
  it('shows the product and foundation status', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: 'Knowledge Browser' })).toBeInTheDocument()
    expect(screen.getByText('Foundation ready')).toBeInTheDocument()
  })
})
```

- [ ] **Step 4: Verify RED**

Run from `web/`:

```bash
npm test -- --run
```

Expected: FAIL because `src/App.tsx` does not exist.

- [ ] **Step 5: Implement the minimal shell**

Create `web/src/App.tsx`:

```tsx
import './styles.css'

export default function App() {
  return (
    <main>
      <h1>Knowledge Browser</h1>
      <p>Foundation ready</p>
    </main>
  )
}
```

Create `web/src/main.tsx`:

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

Create `web/src/styles.css`:

```css
:root {
  color: #202124;
  background: #fff;
  font-family: Inter, ui-sans-serif, system-ui, sans-serif;
}

body {
  margin: 0;
}

main {
  max-width: 48rem;
  margin: 6rem auto;
  padding: 0 1.5rem;
}
```

- [ ] **Step 6: Verify tests and production build**

Run from `web/`:

```bash
npm test -- --run
npm run build
```

Expected: `1 passed`; build exits 0 and creates `web/dist/`.

- [ ] **Step 7: Commit the web foundation**

```bash
git add web
git commit -m "feat: add minimal web foundation"
```

---

### Task 4: Add the local PostgreSQL service

**Files:**
- Create: `compose.yaml`

**Interfaces:**
- Consumes: no application interface in this PR.
- Produces: Compose service `db`, database `${POSTGRES_DB:-knowledge_browser}`, and host port `${POSTGRES_PORT:-5432}` for the later schema feature.

- [ ] **Step 1: Create Compose configuration**

Create `compose.yaml`:

```yaml
services:
  db:
    image: pgvector/pgvector:pg17
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-knowledge_browser}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-postgres}
      POSTGRES_USER: ${POSTGRES_USER:-postgres}
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-postgres} -d ${POSTGRES_DB:-knowledge_browser}"]
      interval: 2s
      timeout: 3s
      retries: 15
    volumes:
      - knowledge_browser_data:/var/lib/postgresql/data

volumes:
  knowledge_browser_data:
```

- [ ] **Step 2: Verify Compose without starting a container**

Run:

```bash
docker compose config --quiet
```

Expected: exit code 0 and no output.

- [ ] **Step 3: Commit the database service**

```bash
git add compose.yaml
git commit -m "chore: add PostgreSQL development service"
```

---

### Task 5: Add pull-request CI

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: API tests, web scripts, and Compose file from Tasks 2–4.
- Produces: required checks named `api`, `web`, and `compose` on pushes and pull requests.

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  api:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip
          cache-dependency-path: api/pyproject.toml
      - run: python -m pip install -e './api[dev]'
      - run: python -m pytest -q api/tests

  web:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: npm
          cache-dependency-path: web/package-lock.json
      - run: npm ci
      - run: npm test -- --run
      - run: npm run build

  compose:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - run: docker compose config --quiet
```

- [ ] **Step 2: Check YAML and referenced commands**

Run:

```bash
rg -n "python -m pytest -q api/tests|npm test -- --run|npm run build|docker compose config --quiet" .github/workflows/ci.yml
docker compose config --quiet
```

Expected: four command matches and Compose exit code 0.

- [ ] **Step 3: Commit CI**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: verify runtime foundation"
```

---

### Task 6: Document setup and verify the PR

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: all commands and ports created by Tasks 2–5.
- Produces: one copy-paste setup path for a new developer and final PR evidence.

- [ ] **Step 1: Replace the short README with exact setup commands**

Write `README.md` with these sections and commands:

````markdown
# Knowledge Browser

Knowledge Browser is an ACL-safe company knowledge search product being rebuilt
through approved feature contracts.

## Current scope

The runtime foundation contains a health-only FastAPI service, a minimal React
shell, PostgreSQL 17 with pgvector for local development, and CI. Search, RAG,
data, database tables, and ACL behavior arrive in later contract-driven PRs.

## Requirements

- Python 3.12
- Node.js 22.22.2 or newer
- Docker with Compose

## API

```bash
python3.12 -m venv api/.venv
api/.venv/bin/python -m pip install -e './api[dev]'
api/.venv/bin/uvicorn knowledge_browser.main:app --reload --app-dir api/src
```

Open `http://127.0.0.1:8000/api/health`.

## Web

```bash
cd web
npm ci
npm run dev
```

Open the Vite URL printed in the terminal.

## PostgreSQL

```bash
docker compose up -d db
docker compose ps
```

Use a different `POSTGRES_PORT` for each parallel worktree.

## Verification

```bash
api/.venv/bin/python -m pytest -q api/tests
(cd web && npm test -- --run)
(cd web && npm run build)
docker compose config --quiet
git diff --check
```

## Product and contribution rules

- Product guardrails: `docs/PRODUCT_INTENT.md`
- Feature contract template: `docs/contracts/FEATURE_CONTRACT_TEMPLATE.md`
- Migration design: `docs/superpowers/specs/2026-08-31-clean-product-migration-design.md`
````

- [ ] **Step 2: Run complete local verification**

Run:

```bash
api/.venv/bin/python -m pytest -q api/tests
(cd web && npm test -- --run)
(cd web && npm run build)
docker compose config --quiet
git diff --check
git status --short
```

Expected:

- API: `1 passed`.
- Web: `1 passed`.
- Build: exit code 0.
- Compose: exit code 0.
- Diff check: exit code 0.
- Status: only the intended README change before its commit.

- [ ] **Step 3: Commit documentation**

```bash
git add README.md
git commit -m "docs: explain runtime foundation setup"
```

- [ ] **Step 4: Confirm the PR contains no forbidden product scope**

Run:

```bash
git diff --name-only origin/main...HEAD
find . -path './.git' -prune -o -path './.worktrees' -prune -o -type f -print | sort
```

Expected: only the files named in the File Map. There must be no `data/`,
`eval/`, database SQL, search module, RAG module, or old report.

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin codex/runtime-foundation
gh pr create \
  --base main \
  --head codex/runtime-foundation \
  --title "Build the runtime foundation" \
  --body $'## Summary\n- add a health-only FastAPI package\n- add a minimal React/Vite shell\n- add PostgreSQL/pgvector development service\n- add API, web, build, and Compose CI\n\n## Contract\n`docs/contracts/runtime-foundation.md`\n\n## Verification\n- API tests\n- web tests and production build\n- Docker Compose config\n- Git whitespace check\n\n## Excluded\nNo search, RAG, data, schema, ACL, or old repository history.'
```

Expected: GitHub returns a pull-request URL targeting `main`.

- [ ] **Step 6: Review and squash merge**

Confirm CI is green and the PR file list matches the contract. Then run:

```bash
gh pr merge --squash --delete-branch
```

After GitHub reports success, fetch `main`, verify the squash commit is on
`origin/main`, remove this worktree from the main checkout, and prune worktree
metadata. Do not begin the canonical dataset feature until this is complete.
