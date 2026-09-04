# Feature contract: ACL gate liveness

## Status

Approved by the user on 2026-09-04.

## User outcome

A passing ACL gate means the audited search reached the documents a leak would
come from. A gate that retrieves nothing fails instead of reporting zero leaks.

## Evidence

- `audit_acl` reported `pairs`, `root_leaks`, and `child_leaks`. None of them
  separate an audit that swept real result sets from one whose search returned
  nothing, which is how both native gates passing a zero query embedding went
  unnoticed until someone looked for it directly.
- `_seed_diverse_acl_shapes` embedded a zero vector. Cosine distance to it is
  undefined, so an HNSW scan never returns those rows and every ACL shape the
  fixture seeds was unreachable on the semantic path, where the ACL predicate
  runs inside the candidate scan rather than around it.
- The entitlement-class audit assumed a representative speaks for its members.
  Nothing checked that assumption against the entitlement oracle.
- `test_native_corpus_entitlement_classes_have_no_acl_leaks` pinned the class
  count to detect a short corpus. A half-loaded import carries a fraction of the
  permission sets and collapses into fewer classes, so that number is satisfied
  by exactly the corpus it was meant to reject.

## Scope

- Return `hits` and `restricted_hits` from `audit_acl`, and assert
  `restricted_hits > 0` in both native gates.
- Verify every class member's allowed-document set equals its representative's.
- Pin the native document count alongside the class count.
- Pin `SearchProfile`'s fields so a new ranking signal fails a test before it can
  silently make class reduction approximate.
- Give the fixture ACL corpus a real embedding and sweep the layered
  direct-plus-group grant through `semantic_search`.

## Non-goals

- Do not change any ACL predicate, the entitlement oracle, ranking, or the
  entitlement-class partition.
- Do not change the Redwood dataset or any golden query.
- Do not add a gate. This tightens the assertions of the gates that exist.

## Dependencies

- `Make ACL evaluation exercise effective retrieval` (#28) on `main`.
- `Give the Redwood corpus twelve permission-set signatures` (#29) on `main`,
  whose ten entitlement classes and twelve signatures these assertions pin.

## Interface and data contract

- `audit_acl`'s result gains two integer keys. `hits` counts every result the
  audit examined; `restricted_hits` counts only those on a document no company
  grant covers. Existing keys keep their meaning.
- A document counts as restricted when its permission entry is a mapping whose
  `visibility` is not `company`, the same shape `entitlement_snapshot` returns.
- Both counters are derived from results the audit already iterates. No
  additional query is issued.

## Safety invariants

- `restricted_hits` is a liveness signal, never an authorization input. It
  cannot suppress, reclassify, or excuse a leak.
- The representative check reads the independent entitlement oracle, not the
  partition that produced the classes.
- A zero query embedding remains rejected rather than substituted.

## Quality and performance

- Both counters are constant work per result the audit already visits, so the
  entitlement-class audit keeps its 2,980 pairs and about 7 minutes.
- The fixture embedding change affects seeded rows only and adds no provider
  call.

## Acceptance criteria

- `audit_acl` over a search that returns nothing reports zero leaks, zero
  `hits`, and zero `restricted_hits`.
- `audit_acl` over results that are all company-wide reports positive `hits` and
  zero `restricted_hits`.
- Both native gates fail when `restricted_hits` is zero.
- The entitlement-class gate fails when any member's allowed-document set
  differs from its representative's.
- Adding or renaming a `SearchProfile` field fails a unit test.
- On the semantic path, the user in both granting groups reaches `SHARED-1` and
  `SECURITY-1`, the group-only user reaches `SHARED-1` but not `SECURITY-1`, and
  no user reaches the memberless group's document.

## Verification

- Unit tier: `api/tests/test_eval_entitlement.py`.
- Integration tier: `api/tests/test_eval_integration.py`.
- `test_native_corpus_entitlement_classes_have_no_acl_leaks` against the loaded
  native database.
- Strict pytest marker collection.

## Implementation inputs

- `api/src/knowledge_browser/eval_entitlement.py`
- `api/src/knowledge_browser/eval_loop.py`
- `api/tests/conftest.py`
- `api/tests/test_eval_entitlement.py`
- `api/tests/test_eval_integration.py`
- `api/tests/test_native_acl.py`
- `docs/contracts/effective-acl-evaluation.md`
