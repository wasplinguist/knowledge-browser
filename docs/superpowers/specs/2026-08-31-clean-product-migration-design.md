# Clean product migration design

## Purpose

Build Knowledge Browser as a clean product repository from the final approved
behavior of the earlier Knowledge Search experiments. The old repository keeps
the research history. This repository keeps a clear product history: one
feature contract, one pull request, and one squash commit per feature.

## Why a clean repository

The earlier repository contains useful final code, but it also contains several
dataset generations, replaced evaluation layouts, temporary reports, merge
commits, and agent checkpoint commits. Rewriting that history into a new
repository would make the result hard to verify. A contract-first rebuild makes
every included behavior explicit and leaves reviewable pull-request records.

## Migration rules

1. The old repository is read-only reference material.
2. Do not copy its `.git` history or push its `main` branch here.
3. Do not use broad directory copies. Select files only after the feature
   contract names them.
4. Prefer rebuilding small files from the approved contract. Reuse final source
   code only when its behavior matches the contract and its tests prove that.
5. Regenerate deterministic data from the approved generator. Do not import old
   generated datasets or archives.
6. Do not import saved eval runs. Produce a fresh baseline after the matching
   product and evaluation features exist.
7. Never import local settings, secrets, caches, screenshots, temporary plans,
   or agent scratch files.
8. Each feature starts from the latest remote `main`, uses its own worktree, and
   ends in a squash merge.

## Pull-request shape

Each feature pull request contains a short review trail:

1. `docs: define <feature> contract`
2. `test: add <feature> acceptance coverage`
3. `feat: implement <feature>`
4. Optional small fixes found by verification or review

The pull request preserves these commits for review. Squash merge gives `main`
one product-level commit. The PR body links the contract and reports exact test
and evaluation results.

## Feature order

Dependencies are merged in this order. A later feature may use only earlier
features already present on remote `main`.

1. **Runtime foundation** — Python API package, React application, PostgreSQL
   development service, basic CI, and health checks.
2. **Existing database compatibility and ACL-safe reads** — connect to and
   verify the populated PostgreSQL/pgvector database, resolve identities, and
   read documents, chunks, and sentences with own-and-root ACL filtering.
3. **Hybrid retrieval** — search profiles, alias expansion, ACL-filtered keyword
   and semantic retrieval, reciprocal rank fusion, and root grouping.
4. **Search API and analytics** — search responses, facets, result identity,
   click events, and safe demo identity behavior.
5. **Grounded RAG answers** — shared retrieval, evidence navigation, citation
   validation, fast/deep budgets, conflicts, completeness, cost, and telemetry.
6. **Web experience** — search loading behavior, answer display, deduplicated
   provenance, and source detail panels instead of broken external demo links.
7. **Enterprise ranking** — only approved freshness, authority,
   personalization, saturation, and hard-query behavior with new evaluation
   evidence.
8. **Evaluation and test tiers** — golden queries, independent entitlement
   checks, retrieval metrics, released/candidate comparison, fast/integration/
   RAG/full-ACL groups, and CI jobs. Generated reports are separate artifacts.
9. **Eval-driven development loop** — the behavior-to-candidate workflow after
   the evaluation system it depends on is merged.

Canonical synthetic dataset generation and source ingestion are deferred. They
may be contracted later for reproducibility or refresh workflows, but neither is
a dependency for compatibility, retrieval, API, RAG, or UI work against the
existing database.

## Data policy

The populated existing database is the active product input. Focused integration
tests use a minimal test-only schema and synthetic rows in a dedicated `_test`
database; that fixture is not a production migration or canonical dataset.

If canonical generation is later approved, its tiny configuration must support
fast tests, its final configuration must support realistic search and
evaluation, and its manifest must prove deterministic hashes and counts.

The migration does not include old tiny corpora, intermediate 400-artifact
data, replaced 1,000-artifact variants, archived data, or historical eval runs.

## Search and ACL gates

Search quality remains the first product priority. Every search-changing
contract identifies evidence, intent, metric, regression risk, golden-set
gaming risk, and unclear purpose. The intent auditor must return `ALIGNED`
before implementation.

ACL filtering happens before ranking or content access. Missing ACL data fails
closed. Child evidence cannot expose a hidden root, and a visible root cannot
expose a hidden child. Fast SQL-backed ACL tests run on relevant pull requests;
the independent full ACL regression must report zero leaks before an ACL or
release change is merged.

## Parallel agent policy

Independent features may run in parallel only when they touch different files
and depend only on merged `main`. Retrieval, API, RAG, and evaluation features
are normally sequential because their contracts depend on one another.
Every worktree uses a unique test database. No agent may commit another
worktree's files or clean another worktree without checking its status.

## Acceptance criteria

- Remote `main` begins with one clean bootstrap commit.
- Every product feature has an approved contract and one focused PR.
- Every feature PR is squash-merged and its branch/worktree is removed after
  verification.
- No old Git commits are imported.
- No inactive dataset, old eval run, local setting, cache, or secret is imported.
- The final product behavior is proved by fresh tests and evaluation, not by old
  reports.
- ACL regression reports zero leaks.
- The old repository remains available as the research archive.
