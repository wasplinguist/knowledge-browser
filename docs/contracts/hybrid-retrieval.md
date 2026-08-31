# Feature contract: hybrid retrieval

## Status

Approved by the clean product migration roadmap.

## User outcome

An allowed user can search the existing company database with keyword and
semantic retrieval. The two ranked lists are combined without mixing their raw
scores, and each root document appears only once.

## Scope

- Versioned search profiles with keyword limit, semantic limit, RRF constant,
  channel weights, alias expansions, and embedding model.
- Whole-term alias expansion that does not rewrite issue keys or larger words.
- PostgreSQL full-text keyword retrieval.
- pgvector sentence retrieval with one semantic rank per chunk.
- Reciprocal rank fusion and one result per canonical root.
- A source filter and short result excerpts.
- ACL filtering on both the matched document and canonical root before ranking.

## Non-goals

- HTTP routes, analytics, answers, UI, evaluation reports, ingestion, or data
  generation.
- Freshness, authority, personalization, saturation, or special hard-query
  ranking. Those belong to Feature 7.
- Query embedding provider calls. The caller supplies an embedding.

## Search intent check

- Real problem: one service or one matching method misses useful company facts.
- Affected intents: known items, facts, troubleshooting, aliases, and
  cross-service evidence.
- Evidence: the validated old product used independent keyword and semantic
  lists, then RRF and root grouping; earlier experiments improved retrieval
  from 39% to 61% overall while preserving strong native-source results.
- Target metric: focused retrieval tests find the allowed expected root in the
  top results with zero ACL leaks.
- Regression risk: semantic matches can weaken exact keyword order, and child
  matches can expose a root incorrectly.
- Golden-set risk: low; this migrates general retrieval behavior and does not
  add query-specific rules.
- Unclear purpose: none.

Intent auditor verdict: `ALIGNED`.

## Safety and behavior

- Unknown users receive no results.
- Missing or denied ACL relationships fail closed in SQL.
- A matched child and its root must both be allowed.
- The joined root must be canonical (`root.root_document_id = root.id`).
- Keyword and semantic raw scores are never added together. RRF uses
  `weight / (rrf_k + rank)`.
- Repeated sentence matches give a chunk only one semantic rank.
- When several chunks map to one root, the result keeps one root and prefers a
  child excerpt because it explains the match.

## Acceptance criteria

- Alias, profile validation, RRF, root grouping, and deterministic tie tests
  pass.
- Focused PostgreSQL tests prove allowed keyword and semantic results.
- Focused PostgreSQL tests prove denied, unknown, and malformed-root content is
  absent.
- Existing Feature 2 tests remain green.
- The focused suite finishes in under one minute locally.

## Verification

- `api/.venv/bin/python -m pytest -q -m "unit or integration" api/tests`
- `DATABASE_URL=... api/.venv/bin/python -m knowledge_browser.db_compat`
- `git diff --check`
