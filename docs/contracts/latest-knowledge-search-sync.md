# Latest Knowledge Search Sync Contract

## Goal

Bring the final useful behavior from the latest `knowledge-search` checkout into
`knowledge-browser` without copying experiment history or changing released
retrieval ranking.

The reference is `/Users/mac/workspace/knowledge-search` at commit `152e8fd`
plus its newer, uncommitted answer and web changes on 2026-09-01.

## Important differences found

- `knowledge-browser` already has the safer root/child ACL search path, exact
  Jira-key ranking, duplicate-source removal, and local document panels.
- The latest reference adds a strict structured AI-answer schema, concise inline
  citation markers, evidence status, conflict notes, missing information, and
  suggested follow-up questions.
- The reference has 603 full native evaluation queries. The clean repo has only
  four small fixture queries and a 603-query text-only ACL projection.
- The reference also contains old generated reports, experiment runs, dataset
  builders, source-ingestion code, aliases, temporal ranking, translation,
  version boosting, and AI reranking. Those are outside this migration.

## Required behavior

### Search and answers

- The result list and the first AI answer start with the same released hybrid
  search implementation and profile.
- The model uses the initial allowed results first. It may call the same hybrid
  search tool again only when those results do not contain enough evidence.
- Every citation must come from a chunk opened through the ACL-safe read path.
- Duplicate citations to the same document appear once.
- Invalid model output, embedding failure, provider failure, and tool failure
  keep the existing safe fallback behavior.
- The provider is asked for a strict JSON schema with answer text and evidence
  metadata in separate fields.

### Web UI

- Show readable answer paragraphs with local inline citation controls.
- Show evidence status, conflicts, missing information, unique sources, and
  suggested next questions.
- Citation and result controls open the ACL-safe local document panel. They do
  not use broken external URLs.
- Follow `/Users/mac/workspace/knowledge-browser/DESIGN.md`: SF/system type,
  17px body text, Action Blue `#0066cc` as the only action color, pill actions,
  flat white/parchment surfaces, 18px utility cards, generous spacing, no
  decorative gradients, and no card/button shadows.
- Keep keyboard focus, 44px touch targets, loading state, and responsive layout.

### Evaluation

- Keep `eval/golden_queries.json` as the fast four-query fixture check.
- Add the latest complete 603-query definitions as `eval/queries.json` for the
  native full retrieval and release gate.
- Do not commit or reuse saved evaluation results.
- A fresh final run reports MRR@10, NDCG@10, Recall@10, forbidden leaks, and
  search latency.
- The full ACL gate checks every configured user against every query and checks
  both returned roots and exact matched child documents/chunks.
- AI citation ACL verification uses an independent entitlement snapshot and
  must report exactly zero leaks.

## Explicit exclusions

- No freshness or temporal boost.
- No aliases, translation, version boost, or AI reranking.
- No source ingestion or destructive schema migration.
- No generated corpus, old report, or old run artifact.
- No claim that this search is better than native Slack, Jira, Confluence, or
  GitHub search.

## Intent audit

Verdict: `ALIGNED`.

- Affected intent: grounded answer presentation and full evaluation coverage.
- Retrieval ordering is unchanged.
- Success: strict structured output, same initial hybrid results, additional
  search only for missing evidence, local allowed citations, fresh full metrics,
  and zero ACL/citation leaks.
- Main risks: provider schema compatibility, citation-number mapping, UI
  accessibility, and native evaluation runtime.
- Golden-set gaming: none; no relevance labels or ranking parameters are edited.

## Acceptance criteria

- Normal API unit and integration tests pass.
- Web tests and production build pass.
- The committed full query file contains exactly 603 unique queries and matches
  the latest reference query fingerprint.
- Fresh full retrieval evaluation completes on the final code.
- Full native ACL scan reports zero root leaks and zero child/chunk leaks.
- AI citation ACL verification reports zero leaks.
- Final whole-feature review, PR CI, squash merge, and worktree cleanup complete.
