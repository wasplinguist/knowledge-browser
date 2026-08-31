# Feature contract: eval-driven development loop

## Status

Approved as Feature 9 in the clean product migration roadmap.

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

`analyze` reads existing events only. `evaluate` accepts a manifest and a new,
empty output directory. It refuses an absent evidence file, non-`ALIGNED`
audit, identical profiles, incomplete embeddings, or an existing output.

The fast ACL sample includes every query marked `acl_aware`, up to two queries
from every query type, each selected query owner, and a stable spread of other
users. It is only a development gate. Native full ACL remains required before
release.

## Decision gate

Recommend the separate release gate only when:

- candidate forbidden leaks are zero;
- fast root and child ACL leaks are zero;
- nDCG@10 improves by at least `0.01`;
- recall@10 does not decrease;
- query losses do not outnumber wins.

Otherwise reject. A recommendation is not promotion; a human reviews the new
report and runs the native full ACL gate.

## Safety invariants

- Behavior analysis does not create searches/clicks or print user content to
  logs beyond the local requested report artifact.
- ACL expected access comes from the independent oracle, not production SQL.
- Search uses the same ACL-filtered hybrid pipeline for both profiles.
- Baseline and challenger use the same database snapshot, queries, users, and
  embeddings.
- Evaluation artifacts include hashes and timestamps proving a new run.
- Released profile is never edited by the runner.
- `UNCLEAR` or `DRIFT` stops before evaluation and requires user direction.

## Product-intent checklist

1. Real user problem: search experiments are easy to disconnect from fresh
   behavior or accidentally “complete” with an old evaluation.
2. Affected intents: all supported retrieval intents; this feature changes the
   experiment process, not ranking.
3. Evidence: approved roadmap plus the missing arbitrary-candidate runner found
   in the baseline exercise.
4. Target metric: every completed loop has fresh behavior, a new candidate, a
   new comparison, fast zero-leak evidence, and an explicit decision.
5. Regression risk: weak validation could bless old results or skip ACL safety.
6. Golden-set gaming: every golden change needs behavior evidence and a written
   reason; unchanged cases remain protected.
7. Unclear purpose: none.

Intent auditor result:

```text
Verdict: ALIGNED
Evidence: approved roadmap and baseline proof that arbitrary fresh candidate evaluation is missing
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

## Source reference

- `knowledge-search/.codex/skills/eval-driven-development/SKILL.md`
- `knowledge-search/api/src/knowledge_search/weekly.py`
- `knowledge-search/scripts/run_eval_loop.py`
