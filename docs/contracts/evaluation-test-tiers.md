# Feature contract: evaluation and test tiers

## Status

Approved by the clean product migration roadmap.

## User outcome

Small changes get a fast, clear test path. Search, RAG, and ACL safety checks
stay available in explicit groups, so faster pull requests do not hide quality
or permission regressions.

## Scope

- Register strict pytest groups for unit, integration, search evaluation, RAG
  evaluation, full ACL, and nightly checks.
- Require every API test to have exactly one primary group.
- Add a small committed golden-query set for deterministic pull-request checks
  and keep the full 603-query definitions for manual/nightly native evaluation.
- Add pure retrieval metrics and released-versus-candidate comparison.
- Add an entitlement oracle that is independent from production ACL SQL.
- Add focused search and RAG evaluation tests with fake providers only.
- Add separate CI jobs for fast API checks and non-nightly evaluation.
- Keep full ACL checks manual/nightly and outside normal pull-request latency.
- Keep generated reports outside Git history and upload CI results as artifacts.

## Non-goals

- Running the exhaustive native-corpus ACL scan in this feature session.
- Importing the old generated corpus or saved evaluation reports. The user later
  approved importing the latest full query definitions, but not saved scores.
- Claiming small fixtures beat Jira, Slack, Confluence, or GitHub search.
- Changing search, ranking, ACL SQL, schema, ingestion, or answer behavior.
- Paid model or embedding calls in tests.

## Test groups

`unit`, `integration`, `search_eval`, and `rag_eval` are primary groups. Every
test has exactly one. `full_retrieval`, `full_acl`, and `nightly` are overlays; a full ACL test
also belongs to `search_eval` and `nightly`.

- `unit`: isolated behavior; no PostgreSQL, network, or large corpus.
- `integration`: PostgreSQL, API, schema, or small-fixture boundaries.
- `search_eval`: retrieval metrics, golden queries, comparison, and search
  quality gates.
- `rag_eval`: grounded-answer evidence and citation quality using fake clients.
- `full_retrieval`: complete 603-query native retrieval quality and latency.
- `full_acl`: exhaustive configured-corpus entitlement/search comparison.
- `nightly`: too slow for normal pull requests.

## Evaluation contract

Each golden query has a stable ID, user, query text, relevant document IDs,
optional source-qualified IDs for small fixtures, optional relevance grades,
and IDs that must never appear. Evaluation reports
MRR@10, nDCG@10, recall@10, and forbidden-result leaks. Comparison reports
per-query wins, losses, unchanged cases, and overall metric deltas.

The independent entitlement oracle consumes permission, direct-user, group,
and membership records. It must not call or import the production ACL SQL
builder. Missing permission data denies access.

## CI and runtime policy

- Every pull request: unit and integration tests, under 5 minutes.
- Every pull request: marker audit, under 1 minute.
- Search/RAG changes and manual runs: non-nightly search and RAG evaluation,
  under 10 minutes.
- Full ACL: manual/nightly only, zero root and child leaks required, timeout 75
  minutes.
- Complete unfiltered API suite: release/nightly, timeout 90 minutes.

The exhaustive full ACL command remains documented and callable, but it is not
run during this implementation because the user explicitly excluded it.

## Safety invariants

- Root and child ACL leaks remain zero.
- Fast default-deny and real-SQL ACL tests stay in pull-request checks.
- Search-quality and grounded-evidence evaluation cannot be silently dropped.
- Strict marker collection fails on missing, misspelled, or multiple primary
  groups.
- Evaluation uses fake model/embedding clients unless a separate manual command
  explicitly says otherwise.
- Search reports are artifacts, not committed product files.

## Product-intent checklist

1. Real user problem: small merges wait for unrelated heavy evaluation.
2. Affected intents: all supported search intents because evaluation protects
   their quality; no ranking behavior changes.
3. Evidence: the earlier suite mixed about 190 ordinary and evaluation tests;
   the native run used hundreds of queries and tens of thousands of ACL pairs,
   taking about 50 minutes.
4. Target metric: fast PR runtime, complete marker coverage, unchanged focused
   search/RAG gates, and zero ACL leaks.
5. Regression risk: a test can be mislabeled and stop running at the correct
   gate.
6. Golden-set gaming: the small committed set is only a PR smoke gate; native
   evaluation remains a separate required release safety check.
7. Unclear purpose: none.

Intent auditor result:

```text
Verdict: ALIGNED
Evidence: measured slow mixed suite and explicit user request to avoid the full native ACL scan during normal work
Affected intents: all supported retrieval intents; evaluation infrastructure only
Metric: PR runtime, marker coverage, retrieval metrics, grounded evidence, zero ACL leaks
Regression risk: mislabeled tests may skip the correct CI gate
Questions: none
```

## Acceptance criteria

- Strict collection succeeds and every API test has one primary group.
- Fast API tests keep existing unit, integration, and ACL coverage green.
- A fresh golden-query evaluation produces MRR@10, nDCG@10, recall@10, and
  leak counts.
- Released/candidate comparison identifies wins and losses.
- Focused grounded-RAG evaluation rejects ungrounded citations.
- CI has separate fast and non-nightly evaluation jobs plus manual/nightly full
  ACL and complete-suite jobs.
- README commands match registered markers.
- No production code behavior changes.
- The exhaustive native ACL scan is not run in this feature session.

## Source reference

- `knowledge-search/docs/handoffs/2026-08-31-api-test-suite-split.md`
- `knowledge-search/api/src/knowledge_search/eval_metrics.py`
- `knowledge-search/api/src/knowledge_search/eval_entitlement.py`
- `knowledge-search/eval/run_eval.py`
