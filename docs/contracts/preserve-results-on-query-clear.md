# Feature contract: preserve results on query clear

## Status

Approved by the user on 2026-09-02.

## User outcome

Clearing the search input prepares a new query without discarding the current
results and grounded answer that the user may still be reading.

## Evidence

- The existing input and clear button both call `clearResults`, immediately
  replacing useful results with the empty-search landing state.
- The user explicitly requested that this local behavior change be committed,
  reviewed, and merged.

## Scope

- Keep the current result and answer state when the input becomes empty.
- Show the landing state only before the first search, not merely because the
  current input is empty.
- Commit the existing visual design reference used by this web experience.

## Non-goals

- Do not change search requests, ranking, ACLs, answer generation, or history.
- Do not add client persistence, caching, or a new search-state abstraction.

## Dependencies

- The current React search experience on `main`.

## Interface and data contract

- Typing or clearing changes only the input value.
- Submitting a non-empty query continues to replace results through the existing
  search flow.
- The clear button remains keyboard accessible and empties the input.

## Safety invariants

- No additional result content is fetched, stored, logged, or exposed.
- Existing demo-identity and document ACL behavior remains unchanged.

## Quality and performance

- The behavior adds no request, dependency, or persistent state.
- Existing search and answer rendering performance is unchanged.

## Acceptance criteria

- Clearing the searchbox after a completed search leaves the current results
  visible.
- The searchbox becomes empty and the initial landing heading stays hidden.
- Initial load still shows the landing state.

## Verification

- Focused React test observed failing before the implementation change.
- Complete web test suite.
- Production web build.

## Implementation inputs

- `web/src/App.tsx`
- `web/src/App.test.tsx`
- `DESIGN.md`
