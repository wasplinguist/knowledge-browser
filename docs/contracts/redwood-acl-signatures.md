# Feature contract: Redwood ACL signatures

## Status

Approved by the user on 2026-09-04.

## User outcome

The ACL gates check the permission shapes an enterprise corpus actually has.
Direct user grants and multi-group unions are exercised by real retrieval
against the native database, not only by fixtures.

## Evidence

- The native corpus resolved to four document ACL signatures: company-wide plus
  three single-group grants. Multi-group unions, direct user grants, and grants
  combining both went unexercised by real retrieval.
- `permission_set_users` had zero rows, so `bulk_verify` reported
  `direct_user_status: not_applicable` with `direct_user_database_links: 0`. Its
  strict authorized and unauthorized direct-user searches never ran.
- `effective-acl-evaluation.md` recorded both facts as corpus limits and pushed
  the missing shapes into the fixture suite, where no real ranking, retrieval,
  or SQL path touches them.

## Scope

- Reshape sixteen company-wide Confluence pages in `data/redwood/` into eight
  new permission-set shapes, taking the corpus from four signatures to twelve.
- Add `scripts/reshape_redwood_acl.py`, which performs that rewrite
  deterministically, and `scripts/regenerate_manifest.py`, which the repository
  lacked.
- Pin the entitlement class count the reshaped corpus produces.
- Correct the statements in `effective-acl-evaluation.md` that this change
  makes false.

## Non-goals

- Do not change group membership. An `acl.jsonl` record's groups must match its
  employee's exactly (`dataset.py:283`), so membership diversity is a two-file
  change and stays out of this one.
- Do not add golden queries, change expected results, or change ranking.
- Do not weaken any ACL predicate, entitlement oracle, or gate.
- Do not represent a grant to a memberless group. Groups exist only through
  employee membership (`dataset.py:250`), so that shape is inexpressible in this
  corpus and stays in the fixture suite.

## Dependencies

- `Make ACL evaluation exercise effective retrieval` (#28) on `main`, which
  added the entitlement-class audit this change re-prices.
- The committed `data/redwood/` dataset and its manifest.

## Interface and data contract

- The eight added shapes: a two-group union, a three-group union, a grant to a
  rare group, a direct grant with no group, a single-user direct grant, a group
  grant plus an outside direct grant, a group grant plus a direct grant that is
  redundant with it, and a second two-group union.
- Every signature lands on two documents, so each proves one permission set can
  govern several documents.
- Documents named by the golden set are never selected.
- Selection is stable under its own edits: a document the script already
  rewrote stays a candidate, so a second run restricts no additional documents.
- `scripts/regenerate_manifest.py` rewrites `data/redwood/manifest.json` from
  the artifact files on disk.

## Safety invariants

- The reshape only widens what the gates check. No document becomes readable by
  a user who could not read it before through some other document.
- `group-private-deployments`, with three members, stands in for the memberless
  group shape that the corpus cannot express.
- The exhaustive `full_acl` gate is unaffected. It sweeps every user regardless
  of how permission sets divide them, so its 2,159,010 pairs still hold.

## Quality and performance

- Measured against a clean import of the reshaped dataset, compared with the
  four-signature database under the same profile and queries: Recall@10 holds at
  0.7541, MRR moves 0.5489 to 0.5513, and search latency does not regress.
- Twelve signatures split the 7,245 employees into ten entitlement classes,
  where four signatures split them into four. The class audit runs one search
  per class and query, so 298 queries cost 2,980 pairs instead of 1,192, about
  7 minutes against the loaded corpus.
- No new golden queries are required. All eight shapes appear in real result
  sets from the existing 298 queries, reaching 13 of the 16 reshaped documents
  across 35 occurrences.

## Acceptance criteria

- The reshaped corpus resolves to twelve distinct permission-set signatures,
  eight of them carrying direct user grants.
- `bulk_verify` reports `direct_user_status: checked` with
  `direct_user_database_links: 7`, and zero leaks.
- The entitlement-class audit reports ten classes whose members sum to 7,245,
  and zero root and child leaks.
- No golden query's expected result changes.
- Running the reshape script twice restricts the same sixteen documents.
- The committed manifest validates against the reshaped artifacts.

## Verification

- `api/tests/test_reshape_redwood_acl.py` and
  `api/tests/test_regenerate_manifest.py`.
- `test_native_corpus_entitlement_classes_have_no_acl_leaks` against the loaded
  native database.
- `./scripts/redwood_database.sh verify` reporting checked direct-user results.

## Implementation inputs

- `scripts/reshape_redwood_acl.py`
- `scripts/regenerate_manifest.py`
- `data/redwood/artifacts/confluence.jsonl`
- `data/redwood/manifest.json`
- `api/tests/test_native_acl.py`
- `api/tests/test_eval_integration.py`
- `docs/contracts/effective-acl-evaluation.md`
