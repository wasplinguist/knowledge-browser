# Feature contract: Inline follow-up answers

## Status

Approved

## User outcome

People can continue asking questions from an AI answer without leaving the
current answer and search results. Suggested and typed follow-ups extend the
answer panel as a readable sequence.

## Evidence

Suggested follow-up buttons currently clear the active results and copy their
text into the global search field, which makes the user leave the answer flow.

## Scope

- Submit suggested follow-ups from the current AI answer.
- Provide a text field for arbitrary follow-up questions.
- Append each question and grounded response to the current answer panel.
- Preserve the initial query, answer, and result list.
- Show loading and error feedback beside the submitted follow-up.

## Non-goals

- Do not add server-side conversation memory or change retrieval behavior.
- Do not persist or restore answer conversations.
- Do not change the global search form or result ranking.

## Dependencies

The existing search, answer, citation, and local document-panel behavior on
`main`.

## Interface and data contract

Suggested buttons and the inline form both send the selected text to the
existing `POST /api/answer` endpoint with the active demo user and source
filter. Each local follow-up entry contains its submitted question and one of
three states: loading, a complete `AnswerResponse`, or an error message.

## Safety invariants

- The active demo-user identity and source filter apply to every follow-up.
- Citations open only through the existing ACL-checked local document route.
- A failed follow-up must not remove an earlier answer or expose raw errors.

## Quality and performance

The feature adds no API round trips beyond one existing answer request per
submitted follow-up and introduces no dependencies.

## Acceptance criteria

- Clicking a suggested question leaves the global query and search results in
  place and appends the question and its answer inside the AI answer panel.
- Submitting a non-empty custom question follows the same path.
- Empty or whitespace-only questions are not submitted.
- Only one follow-up can be submitted while the current one is loading.
- A failed follow-up displays an inline error while earlier content remains.

## Verification

- Focused web component tests cover suggested and typed follow-ups.
- The complete web test suite and production build pass.
- Manual review confirms keyboard focus and responsive layout.

## Implementation inputs

- `web/src/App.tsx`
- `web/src/App.test.tsx`
- `web/src/styles.css`
- `web/src/api.ts`
