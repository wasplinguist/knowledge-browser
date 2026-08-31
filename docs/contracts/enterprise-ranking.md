# Feature contract: enterprise ranking

## Status

Approved by the clean product migration roadmap and the product-intent audit
below.

## User outcome

An allowed user gets more useful ordering for recent status questions, exact
Jira keys, source-specific questions, and work related to their primary
project, while ordinary historical queries keep their relevance order.

## Evidence

- The user explicitly requested freshness checks for `latest`, `newest`,
  `most recent`, and `current`.
- Old validated behavior showed a stale Confluence plan above the current Jira
  status, exact Jira tickets below cross-service mentions, and an unrelated
  project above the user's primary project.
- Earlier hard troubleshooting queries showed why one strong clue must not turn
  into a broad AND-only query or a new candidate source. This feature therefore
  reranks retrieved candidates only.

## Scope

- Add profile-controlled freshness, source-authority, exact-Jira-key, and
  primary-project weights.
- Apply freshness only to explicit recent-information queries.
- Rank with the newest matching child or root timestamp.
- Apply source authority only when the query clearly names a source-owned fact.
- Boost an exact Jira key only when the Jira `issue_metadata` evidence contains
  that complete key.
- Read primary-project context from existing indexed user/document metadata.
- Release the validated weights in the shared search profile used by result
  lists and first-answer retrieval.
- Add a small fresh baseline-versus-candidate comparison for the approved cases.

## Non-goals

- New retrieval channels, candidate expansion, query generation, LLM ranking,
  schema changes, ingestion, or broad hard-query heuristics.
- Changing the existing RAG coverage-saturation rule.
- Claiming fixture results beat real Jira, Slack, Confluence, or GitHub search.
- Building the full evaluation harness or running the exhaustive native ACL
  scan; those belong to Feature 8.

## Dependencies

- Existing database compatibility and ACL-safe reads.
- Hybrid retrieval and released profile loading.
- Search API, grounded RAG, and web experience.

## Interface and data contract

`SearchProfile` adds four non-negative numeric fields, all defaulting to zero:
`freshness_weight`, `authority_weight`, `jira_key_weight`, and
`personalization_weight`.

The released profile enables the already validated values:

- freshness: `0.05`
- authority: `0.05`
- exact Jira key: `1.0`
- personalization: `0.05`

All boosts use RRF-style rank contributions and only modify candidates already
returned by keyword or semantic retrieval.

## Safety invariants

- ACL filtering remains inside keyword/semantic SQL before reranking.
- Ranking never loads or adds an inaccessible candidate.
- Missing project metadata gives no personalization boost.
- A child timestamp can affect its visible root only when both child and root
  already passed ACL filtering.
- Search result and first-answer retrieval continue to use the same profile and
  `hybrid_search` function.

## Product-intent checklist

1. Real user problem: current, exact-ticket, and own-project answers can be
   buried below weaker but lexically similar results.
2. Affected intents: recent information, status, known item, acronym/alias,
   personalization, and hard troubleshooting.
3. Evidence: the user request plus the old validated ranking failures listed
   above.
4. Target metric: focused top-1 wins with zero losses on protected historical
   cases and zero focused ACL leaks.
5. Regression risk: historical and cross-service questions may over-prefer a
   new or source-specific result.
6. Golden-set gaming: mitigated by query-aware rules, negative historical and
   partial-key cases, and a later full evaluation gate. Fixture evidence remains
   only relative evidence.
7. Unclear purpose: none.

Intent auditor result:

```text
Verdict: ALIGNED
Evidence: explicit freshness request and old validated stale-status, exact-key, and primary-project ranking failures
Affected intents: recent_information, status, known_item, acronym_alias, personalized, troubleshooting
Metric: focused top-1 wins, protected-query losses, focused ACL leaks
Regression risk: historical and cross-service queries may be over-boosted
Questions: none
```

## Acceptance criteria

- `latest`, `newest`, `most recent`, and `current` prefer newer matching
  evidence.
- A non-temporal historical query keeps the original relevance order.
- A clear status query gets a small Jira authority boost.
- An exact Jira key beats a cross-service mention and a longer partial key does
  not get the exact boost.
- A retrieved primary-project result can move above an equally relevant other
  project result.
- Disabled weights preserve the Feature 3 order and behavior.
- Focused baseline-versus-candidate evidence reports more wins than losses and
  no protected-query loss.
- Existing ACL tests remain green; no exhaustive native ACL scan is run.

## Verification

- `TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/knowledge_browser_enterprise_ranking_test api/.venv/bin/python -m pytest -q -m "unit or integration" api/tests`
- focused ranking comparison recorded in the pull request
- `npm test -- --run` and `npm run build` from `web/`
- `docker compose config --quiet`
- `git diff --check`
- one final whole-feature review

## Source reference

- `knowledge-search/api/src/knowledge_search/search.py`
- `knowledge-search/api/src/knowledge_search/profiles.py`
- `knowledge-search/api/tests/test_profiles.py`
- `knowledge-search/api/tests/test_search_api.py`
- `knowledge-search/eval/profiles/enterprise-v1.json`
- `knowledge-search/eval/experiments/exp-2026-08-31-exact-jira-key/experiment.json`
