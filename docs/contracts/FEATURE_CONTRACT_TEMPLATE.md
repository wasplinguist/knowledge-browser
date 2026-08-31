# Feature contract: <name>

## Status

Proposed | Approved | Implemented

## User outcome

State the visible problem this feature solves in one short paragraph.

## Evidence

List the behavior, query, user report, or measurement that justifies the work.

## Scope

- List behavior included in this pull request.
- Keep one contract focused on one feature.

## Non-goals

- List nearby behavior that this pull request will not add or change.

## Dependencies

List required features already merged into `main`. Do not depend on an
unmerged sibling worktree.

## Interface and data contract

Define inputs, outputs, errors, stored data, and compatibility requirements.
Use concrete examples when a shape could be misunderstood.

## Safety invariants

- Define ACL and privacy rules.
- Define default-deny behavior.
- Define what must never be logged or returned.

## Quality and performance

Define the quality metric, released baseline, latency target, and allowed cost.
For search changes, include the completed product-intent checklist and intent
auditor verdict.

## Acceptance criteria

- Write observable pass/fail requirements.
- Include failure and empty-result behavior.

## Verification

List focused tests, integration tests, evaluation, build checks, and any manual
review required before squash merge.

## Source reference

List old repository files used only as reference. Do not import old Git history,
inactive datasets, old reports, or experimental artifacts.
