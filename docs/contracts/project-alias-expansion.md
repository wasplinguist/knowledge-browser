# Feature contract: project alias expansion

## Status

Candidate implemented; fresh Redwood evaluation pending.

## User outcome

People can search with a project's short name or source-specific name and get
the same retrieval behavior as when they use its canonical project name.

## Evidence

- The user explicitly requested profile-based whole-term alias expansion.
- `data/redwood/projects.jsonl` now provides the repository-owned project
  names, Jira keys, and per-source aliases used as the authoritative catalog.
- The replacement 298-question Redwood benchmark does not contain a dedicated
  alias family, so promotion requires a fresh, separately reviewed experiment.

## Scope

- Add a challenger search profile with project alias-to-canonical-name
  expansions derived from the current project catalog.
- Reuse the existing whole-term, short-uppercase, and Jira issue-key protection
  in `expand_query`.
- Evaluate the released and challenger profiles against the same 298 questions,
  users, database snapshot, embedding model, and embedding configuration. Each
  profile embeds its effective query text, matching the production API: raw for
  released and whole-term-expanded for the challenger.
- Run the deterministic fast ACL sample and normal pull-request regression
  checks.

## Non-goals

- Do not change `search/profiles/released.json` or automatically promote the
  challenger.
- Do not run the exhaustive full ACL release gate in this work.
- Do not change golden questions, relevance labels, embeddings, ranking
  weights, retrieval SQL, or answer generation.
- Do not add runtime project discovery, an alias administration UI, or fuzzy
  alias matching.

## Dependencies

- Portable first-run database bootstrap and the Redwood dataset are available
  on `main`.
- The released hybrid retrieval and profile expansion behavior are already on
  `main`.

## Interface and data contract

- The challenger is a JSON `SearchProfile` under
  `search/profiles/candidates/`.
- `query_expansions` maps a catalog alias to the project's canonical `name`.
- Matching is limited to whole terms. Two-character uppercase aliases remain
  case-sensitive, so `DB` may expand while `db` does not.
- A Jira issue key such as `NIMREL-401` remains unchanged; a standalone
  `NIMREL` may expand to `Nimbus Relay`.
- Aliases that resolve to more than one canonical project must not be included.
- An alias already present as a whole term inside its canonical project name is
  excluded, because expanding it would duplicate part of an already canonical
  query. Source slugs, Program names, Jira keys, and repository paths remain.
- The released profile remains the default API profile.

## Safety invariants

- Expansion must not bypass, weaken, or move ACL predicates. Both keyword and
  semantic retrieval continue to use the existing ACL-filtered search paths.
- The challenger must have zero forbidden golden-result leaks and zero root or
  child leaks in the fast ACL sample.
- Inaccessible project names or documents must not be exposed through result
  metadata or logs.

## Quality and performance

### Product-intent checklist

1. Real user problem: project acronyms and cross-source names currently fail to
   retrieve evidence found by canonical project names.
2. Affected intent: acronyms and aliases, known items, and cross-service
   evidence.
3. Evidence: the current project catalog yields 82 unambiguous whole-term
   expansions, while the replacement benchmark has no dedicated alias family.
   Fresh behavior evidence and alias questions are required before promotion.
4. Target metric: improve alias nDCG@10 and Recall@10; do not reduce overall
   Recall@10 or introduce forbidden/ACL leaks.
5. Regression risk: common uppercase terms including `PR`, `DB`, `QA`, `CI`,
   `IS`, and `ME` can have non-project meanings.
6. Golden-set gaming: aliases come only from the repository-owned project
   catalog, not from golden relevance labels. All 298 questions are evaluated
   for regressions.
7. Unclear purpose: none. The user selected profile-based whole-term expansion.

### Intent auditor

```text
Verdict: UNCLEAR
Evidence: The current catalog yields 82 candidate expansions, but the replacement benchmark has no alias family.
Affected intents: acronyms and aliases, known items, cross-service evidence
Metric: alias nDCG@10 and Recall@10; overall Recall@10; forbidden and ACL leaks
Regression risk: common two-letter abbreviations such as PR, DB, and QA can have non-project meanings
Questions: collect current alias failures and add an independently labeled alias evaluation before promotion
```

- The candidate should improve alias nDCG@10 and Recall@10.
- Overall Recall@10 must not decrease; losses must not exceed wins.
- Candidate wall-clock evaluation time must be no more than 20% or 250 ms over
  the released profile, whichever allowance is larger.
- Fixture results show relative improvement only; they do not prove superiority
  over native Slack, Jira, Confluence, or GitHub search.

## Acceptance criteria

- Every included alias expands to its canonical catalog name as a whole term.
- Lowercase forms of two-character uppercase aliases do not expand.
- Jira issue keys and longer words containing an alias do not expand.
- The candidate profile is loadable and behaviorally distinct from released.
- The same 298-question evaluation reports metrics by question family and no
  forbidden leaks.
- Fast ACL reports no root or child leaks.
- Released profile and default API behavior are unchanged.

## Verification

- Focused profile expansion unit tests, observed red before green.
- Non-nightly API unit and integration suites.
- Web tests and production build.
- Fresh 298-question released-versus-challenger retrieval comparison using one
  database snapshot and the same embedding model and configuration, with each
  profile embedding its effective query text, summarized by question family.
- Deterministic fast ACL sample against the challenger.
- Full ACL is explicitly deferred to a later human-reviewed release gate.

### Historical verification results

These results belong to the superseded 603-question benchmark. They explain
why the candidate exists but do not validate it against the current Redwood
corpus or authorize promotion.

- On 2026-09-01, the 603-question comparison improved overall nDCG@10 from
  `0.59859` to `0.61217` and Recall@10 from `0.71012` to `0.73333`, with 22
  wins, 2 losses, 579 ties, and zero forbidden leaks.
- The 25-question alias slice improved nDCG@10 from `0.12967` to `0.45723`
  and Recall@10 from `0.36` to `0.92`. Every other question family was
  unchanged.
- Released took 35,121 ms and the challenger took 35,606 ms on the same
  read-only snapshot, a 485 ms (1.4%) increase within the allowed threshold.
- Fast ACL checked 224 pairs with zero root or child leaks.
- Full ACL was not run and the challenger was not promoted.

## Alias data source

The only alias source is the committed `data/redwood/projects.jsonl` catalog.
