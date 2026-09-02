# Product intent

## Product priority

Knowledge Browser gives people better practical company-knowledge search than
searching Slack, Jira, Confluence, and GitHub one by one. Search quality is the
first product priority. AI answers are useful only after retrieval is useful.

## Supported query intents

Search supports known items, facts, people and owners, project status,
decisions and reasons, troubleshooting, cross-service evidence, acronyms and
aliases, recent information, and practical questions involving tacit terms,
comments, replies, reviews, or ownership.

## Guardrails

Keep ACL filtering correct and hide the existence of inaccessible content.
Preserve acceptable latency. Evaluate search results, not answer wording.
Fixture evaluation proves only relative improvement over the released profile;
it does not prove that Knowledge Browser beats real native-service search.

## Non-goals for v1

V1 does not add an admin UI, scheduler, hosted analytics, automatic promotion,
real connectors, answer evaluation, production identity and privacy
infrastructure, PostgreSQL RLS, or a large platform test matrix. A human reviews
every possible search release.

## Intent checklist

Before changing search behavior, record:

1. The real user problem.
2. The affected query intent.
3. Evidence for the problem.
4. The target metric.
5. Regression risk to another intent.
6. Whether the change only games the golden set.
7. Any unclear product purpose.
