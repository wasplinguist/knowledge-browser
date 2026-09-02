# Feature contract: eval-driven development loop

## Status

Implemented.

## User outcome

A future coding session can turn fresh search behavior into one new challenger,
run a fresh comparison, and make a clear reject-or-release-gate recommendation
without reusing old results or promoting a profile automatically.

## Scope

- Add a short repository skill and natural-language trigger.
- Add a read-only weekly behavior report from existing search/click events.
- Exclude named synthetic profiles and record those exclusions.
- Validate an experiment manifest, evidence, `ALIGNED` intent audit, distinct
  baseline/challenger profiles, golden queries, and query embeddings.
- Run a new same-query released-versus-candidate retrieval evaluation.
- Record profile, query, evidence, and run hashes/provenance.
- Run a deterministic fast ACL sample during development.
- Write fresh JSON and easy-English HTML artifacts in a new output directory.
- Decide `reject` or `recommend-release-gate`; never promote automatically.

## Non-goals

- Running a real search experiment without fresh behavior evidence.
- Inventing a challenger in this infrastructure feature.
- Reusing an old eval file as a new result.
- Running the exhaustive native ACL gate in this feature session.
- Automatic profile promotion, answer evaluation, paid provider calls, or
  generated reports committed to Git.

## Workflow contract

```text
Behavior → Insight → Hypothesis → Develop Challenger → New Eval → Compare → Decision
```

Every arrow must be backed by a fresh artifact or implemented change. An old
run, an unchanged profile, a fixture-only ACL pass, or a report joined to a new
manifest does not complete the loop.

`analyze` reads existing events only. `evaluate` accepts a manifest and an
output path that does not exist yet. It refuses an absent, empty, malformed, or
older-than-24-hours evidence report; a non-`ALIGNED` audit; a baseline other
than `search/profiles/released.json`; a challenger with unchanged behavior
settings; incomplete embeddings; or any existing output directory.

A useful behavior report must have the complete weekly-report shape and at
least one no-result query, unclicked query, or reformulation. The manifest's
insight, hypothesis, implementation, regression risk, audit evidence, and
golden decision must be real non-empty explanations. Every golden change is an
object with `query_id`, `change`, and behavior `evidence`.

The fast ACL sample includes every query marked `acl_aware`, up to two queries
from every query type, each selected query owner, and a stable spread of other
users. It is only a development gate. Native full ACL remains required before
release.

Evaluation uses one read-only repeatable-read transaction, so baseline,
challenger, and ACL checks see one database snapshot. Input hashes are taken
before search and checked again afterward. Any changed input fails the run.
The feature code and manifest must be committed first. Evaluation refuses a
dirty worktree, records a hash of every tracked file, and checks the hash again
afterward. Generated output is rejected inside every checkout/worktree and the
shared `.git` directory.

## Manifest example

Save a challenger profile under `search/profiles/candidates/` and an experiment
manifest under `eval/experiments/<id>/experiment.json`. Evidence and embeddings
may be absolute local paths outside Git. Profiles and golden queries must stay
inside this repository.

```json
{
  "id": "2026-09-01-nrel-alias",
  "created_at": "2026-09-01T09:00:00Z",
  "evidence_report": "/tmp/knowledge-browser-behavior/20260901T000000Z-weekly.json",
  "insight": "People reformulate NREL as Nimbus Relay.",
  "hypothesis": "The whole-term alias improves nDCG@10 by at least 0.01.",
  "implementation": "Expand the exact whole term NREL to Nimbus Relay.",
  "affected_intents": ["acronym_alias"],
  "target_metrics": ["ndcg@10", "recall@10", "acl_leaks"],
  "regression_risk": "Unrelated uses of NREL may move.",
  "intent_audit": {"verdict": "ALIGNED", "evidence": "weekly reformulation"},
  "baseline_profile": "search/profiles/released.json",
  "challenger_profile": "search/profiles/candidates/nrel-alias.json",
  "golden_queries": "eval/fixture_queries.json",
  "query_embeddings": "/tmp/knowledge-browser-behavior/query-embeddings.json",
  "golden_changes": [],
  "golden_change_reason": "Existing labels already measure this behavior.",
  "status": "implemented"
}
```

Every golden query ID needs one 1,536-number vector in the embeddings JSON.
Repeated query text must use the same vector. An unclicked query alone does not
prove an alias or a relevance label; keep the uncertainty or stop.

When a label really changes, use this form:

```json
{"query_id": "q-nrel", "change": "add relevant Jira root", "evidence": "reformulation and click in the fresh weekly report"}
```

## Decision gate

Recommend the separate release gate only when:

- candidate forbidden leaks are zero;
- fast root and child ACL leaks are zero;
- nDCG@10 improves by at least `0.01`;
- recall@10 does not decrease;
- query losses do not outnumber wins.
- challenger runtime is not more than the larger of 20% or 250 ms above the
  baseline for the same evaluation queries.

Otherwise reject. A recommendation is not promotion; a human reviews the new
report and runs the native full ACL gate.

## Safety invariants

- Behavior analysis does not create searches/clicks or print user content to
  logs beyond the local requested report artifact.
- ACL expected access comes from the independent oracle, not production SQL.
- Search uses the same ACL-filtered hybrid pipeline for both profiles.
- Baseline and challenger use the same database snapshot, queries, users, and
  embeddings.
- Evaluation artifacts include manifest/input hashes, the command, Git commit,
  and timestamps proving a new run; changing any input during evaluation fails.
- Released profile is never edited by the runner.
- `UNCLEAR` or `DRIFT` stops before evaluation and requires user direction.

## Product-intent checklist

1. Real user problem: search experiments are easy to disconnect from fresh
   behavior or accidentally “complete” with an old evaluation.
2. Affected intents: all supported retrieval intents; this feature changes the
   experiment process, not ranking.
3. Evidence: the baseline exercise showed that arbitrary fresh candidate
   evaluation was missing.
4. Target metric: every completed loop has fresh behavior, a new candidate, a
   new comparison, fast zero-leak evidence, and an explicit decision.
5. Regression risk: weak validation could bless old results or skip ACL safety.
6. Golden-set gaming: every golden change needs behavior evidence and a written
   reason; unchanged cases remain protected.
7. Unclear purpose: none.

Intent auditor result:

```text
Verdict: ALIGNED
Evidence: baseline proof that arbitrary fresh candidate evaluation is missing
Affected intents: all supported retrieval intents; experiment infrastructure only
Metric: fresh-run provenance, comparison metrics, fast ACL leaks, explicit decision
Regression risk: stale results or weak samples could be mistaken for release proof
Questions: none
```

## Acceptance criteria

- Weekly reports are deterministic, read-only, and record excluded profiles.
- Invalid, stale, unchanged, or non-`ALIGNED` experiments fail closed.
- Fast ACL sampling is deterministic and smaller than full native evaluation.
- One invocation creates exactly one fresh JSON run and one HTML report.
- Report contains evidence, hypothesis, implementation, hashes, metrics,
  wins/losses, ACL result, and decision in easy English.
- Runner never edits `search/profiles/released.json`.
- Skill validation and forward scenario pass.
- Unit, integration, non-nightly evaluation, web, build, and CI pass.
- Native full ACL is not run in this feature session.
