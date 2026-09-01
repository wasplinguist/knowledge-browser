# Feature contract: Grounded answer provenance

## Status

Approved

## User outcome

When Knowledge Browser gives a factual AI answer from retrieved company data,
the answer also shows at least one source that the user is allowed to open.

## Evidence

The query `What measures were taken to resolve the release controller schema
lock latency?` returned a factual answer and 19 search results, but no Sources
section. A debug run showed one successful `hybrid_search` call,
`opened_chunks=0`, `citations=[]`, and `evidence_status=incomplete`.

## Scope

- Require the answer workflow to open ACL-safe retrieved evidence before it
  returns a factual answer.
- Require a factual answer backed by retrieved results to contain at least one
  citation that resolves to successfully opened evidence.
- Keep the existing web provenance UI and document panel unchanged.

## Non-goals

- Do not change retrieval ranking, profiles, source filters, or search results.
- Do not add fallback citations from unopened snippets.
- Do not change ACL rules, demo identity, or document detail behavior.
- Do not run `full_acl`, `full_retrieval`, or `nightly` tests.

## Dependencies

- Grounded answer generation and ACL-safe citation resolution already merged
  into `main`.
- The existing web Sources section already renders nonempty API citations.

## Interface and data contract

`POST /api/answer` keeps its existing request and response shapes. When allowed
initial search results exist, answer generation must first request a
`read_chunk` tool call. A returned factual answer must have at least one item in
`citations`, and each citation must resolve to a chunk successfully returned by
`read_chunk` for the selected user. If no citation resolves, answer generation
fails through the existing safe `answer_unavailable` response instead of
returning an unsupported factual answer.

When retrieval returns no results, the existing incomplete/no-evidence answer
may still have no citations because there is no company fact to attribute.

## Safety invariants

- Never cite search snippets or unopened chunks.
- `read_chunk` must re-check access for the selected demo user.
- Never reveal the existence, identifier, title, URL, or text of inaccessible
  evidence.
- Never invent or automatically attach a source the model did not cite.
- Provider and database errors keep the existing safe public error shape.

## Quality and performance

Product-intent checklist:

1. Real user problem: a factual answer was visible without provenance.
2. Affected intents: facts and troubleshooting questions.
3. Evidence: the reproduced query found 19 results but opened zero chunks and
   returned zero citations.
4. Target metric: 100% of returned factual answers with retrieved evidence
   contain at least one ACL-safe opened citation; ACL leaks remain zero.
5. Regression risk: one required evidence read can add latency or select a weak
   result; existing tool budgets and ranking limit this risk.
6. Golden-set gaming: no; the rule applies to every answer query.
7. Unclear product purpose: none.

Intent auditor:

```text
Verdict: ALIGNED
Evidence: A live user query returned a factual answer after hybrid search but opened 0 chunks and returned 0 citations.
Affected intents: facts, troubleshooting
Metric: 100% of factual answers with retrieved results have at least one ACL-safe opened citation; zero ACL leaks
Regression risk: a required read may add latency or open a weak top result
Questions: none
```

The forced evidence read may add one provider continuation and one database
read when a model previously answered directly. It adds no extra retrieval
embedding when initial results exist and stays inside the existing fast/deep
tool-call budgets. The reproduced fast query took 13.4 seconds before the fix
and 11.4 seconds after the fix in one local run. The acceptance ceiling for the
same local fast query is 20 seconds; this single-run check is not a production
latency percentile.

## Acceptance criteria

- With initial results, the first model request requires `read_chunk`.
- A supported answer returns citation metadata and the web Sources section is
  visible through existing rendering behavior.
- An answer with initial results but no resolved citation fails closed.
- No-result answers keep the current incomplete/no-citation behavior.
- Existing citation alias handling, conflict handling, and ACL tests pass.

## Verification

- Focused unit tests for required evidence opening and fail-closed citations.
- Existing answer, RAG evaluation, and API route tests.
- Normal API tests with `full_acl`, `full_retrieval`, and `nightly` excluded.
- Existing web tests and production build.
- `git diff --check`.

## Source reference

No old repository files or datasets are used.
