# Feature contract: answer document context

## Status

Implemented after user approval on 2026-09-01.

## User outcome

When an AI answer opens one allowed document chunk, it can also use related
content chunks from that document. A root cause or resolution that exists on
the same page is not reported as missing only because another section matched
the question first.

## Evidence

The query `why release controller certificate rotation failure?` opened only
the Impact chunk from Confluence document
`artifact-059-confluence-postmortem`. The same document has separate Root cause
and Resolution body chunks, but the answer did not receive them. The document
panel did receive the full page and showed both sections.

## Scope

- Expand an allowed chunk read with sibling chunks from the same document and
  field.
- Keep the existing fast and deep read budgets.
- Make every expanded chunk available for exact citation validation.
- Add focused answer-loop and ACL-safe read tests.

## Non-goals

- Do not change hybrid ranking, search result order, source ingestion, chunking,
  prompts unrelated to document context, or the web layout.
- Do not run or change the native `full_acl` gate.
- Do not add a new dependency or a new API endpoint.

## Dependencies

- Grounded RAG answers.
- Grounded answer provenance.
- Existing database compatibility and ACL-safe reads.

## Interface and data contract

`read_chunk` still accepts a source and chunk ID. Internally, the answer loop
may receive more allowed chunks from the same source document and field, up to
the mode's existing read limit. The public `POST /api/answer` response shape is
unchanged. Citations still contain exact chunk IDs.

## Safety invariants

- Both the selected document and its canonical root must pass the existing ACL
  checks before any sibling text is returned.
- Siblings must come from the selected chunk's document, source, and field.
- Denied or malformed document chains return no context and reveal nothing.
- Raw evidence text must not be added to logs or debug traces.

## Quality and performance

Product-intent checklist:

1. Real problem: an answer misses evidence present in another section of the
   cited document.
2. Affected intent: decisions and reasons, troubleshooting, and project status.
3. Evidence: the recorded Confluence query and its opened Impact chunk.
4. Target metric: all relevant same-field chunks within the existing read
   budget are available to the answer loop.
5. Regression risk: a long document could use the read budget before another
   document is opened.
6. Golden-set risk: no golden labels or ranking rules change.
7. Unclear purpose: none.

Intent auditor:

```text
Verdict: ALIGNED
Evidence: The recorded certificate-rotation query opened the Impact chunk while Root cause and Resolution body chunks existed in the same allowed Confluence document.
Affected intents: decisions and reasons; troubleshooting; project status
Metric: related same-field evidence chunks made available within the existing read budget
Regression risk: long documents can consume the read budget
Questions: none
```

The existing read budget bounds model input and latency. Search result ranking
and the released search profile do not change. One context read uses one
bounded database query.

## Acceptance criteria

- Opening one chunk also opens allowed same-document, same-field chunks until
  the existing read limit is reached.
- The selected chunk is always kept first.
- Expanded chunks can be cited by exact chunk ID.
- Repeated reads do not resend chunks that are already open.
- An inaccessible child or root produces no selected or sibling evidence.
- Existing answer, search, and web checks pass without the `full_acl` gate.

## Verification

- Focused unit tests for answer context expansion and citations.
- Focused integration tests for sibling selection and ACL denial.
- API unit and integration tests with a worktree-specific test database.
- Web tests and production build.
- `docker compose config --quiet`.
- `git diff --check`.

## Source reference

No old repository files are copied or used.
