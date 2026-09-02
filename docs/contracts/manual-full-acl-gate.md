# Feature contract: manual full ACL gate

## Status

Approved by the user on 2026-09-02.

## User outcome

Nightly CI remains bounded and actionable while the exhaustive native-corpus
ACL matrix stays available as an explicit, human-reviewed release gate.

## Evidence

- The full ACL matrix multiplies every configured user by every golden query and
  is intentionally separate from the fast ACL coverage used during development.
- The user requested that the current local nightly-policy change be committed,
  reviewed, and merged.

## Scope

- Remove the exhaustive `full_acl` invocation from the nightly workflow.
- Keep the `full_acl` marker and test callable for manual release verification.
- Clarify that search quality is the product priority rather than an abstract
  north-star label.

## Non-goals

- Do not remove, weaken, or change any ACL predicate or entitlement oracle.
- Do not change search ranking, evaluation labels, or the Redwood dataset.
- Do not replace fast ACL checks with fixture-only ranking evidence.

## Dependencies

- Existing fast ACL tests, independent entitlement oracle, and manual
  `full_acl` test on `main`.

## Interface and data contract

- Normal and nightly CI continue to exclude `full_acl` from their complete API
  suite.
- Operators can still run `python -m pytest -q -m full_acl api/tests` with the
  required native database configuration.

## Safety invariants

- Pull-request ACL tests, unknown-user default deny, root/child checks, and
  forbidden-result checks remain enabled.
- Removing an automatic schedule does not alter production authorization.

## Quality and performance

- Nightly CI no longer incurs the exhaustive user-query matrix runtime.
- Manual release approval still requires zero root and matched-child leaks.

## Acceptance criteria

- The nightly workflow contains no `-m full_acl` command.
- The full ACL test and marker remain collected.
- Existing normal API and evaluation tiers pass unchanged.

## Verification

- Strict pytest marker collection.
- Normal API and evaluation tiers.
- Workflow diff inspection and GitHub CI.

## Implementation inputs

- `.github/workflows/nightly.yml`
- `api/tests/test_native_acl.py`
- `api/pyproject.toml`
- `docs/PRODUCT_INTENT.md`
- `docs/contracts/evaluation-test-tiers.md`
