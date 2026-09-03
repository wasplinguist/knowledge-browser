# Feature contract: effective ACL evaluation

## Status

Approved by the user on 2026-09-03.

## User outcome

The ACL gates prove something: they run a search that actually retrieves the
sensitive documents, they cover every user, and they finish in minutes rather
than a day and a half.

## Evidence

- Both native gates passed a zero vector as the query embedding, which disables
  the semantic half of hybrid search. Measured against the loaded corpus, the
  restricted document a question targets was retrievable by an authorized user
  in 3 of 24 `acl_denied` cases with a zero vector, and 23 of 24 with the real
  embedding. The audited result sets never contained the documents a leak would
  come from.
- The exhaustive matrix is 7,245 users x 298 queries = 2,159,010 pairs at ~62 ms
  per pair, about 37 hours on a developer machine. The corpus resolves to four
  distinct permission-set signatures, so 2,157,818 of those pairs re-test a
  combination already covered.
- `select_fast_acl_inputs` selected ACL questions by an `acl_aware` field that
  no Redwood golden query carries, so the eval loop's fast ACL sample contained
  no ACL question at all.
- The corpus cannot reach several ACL branches: `permission_set_users` has zero
  rows, no permission set names more than one group, and no document has a
  parent, so the root check duplicates the child check.

## Scope

- Cache golden query embeddings and use them in both native gates.
- Add an entitlement-class ACL audit that covers every user through one
  representative per class, and keep the exhaustive matrix as the manual gate.
- Select ACL questions by the field the leak check consumes.
- Extend the fixture corpus with the ACL shapes the native corpus omits, and
  score the fixture golden set against every one of them.

## Non-goals

- Do not remove, weaken, or change any ACL predicate or entitlement oracle.
- Do not change search ranking, evaluation labels, or the Redwood dataset.
- Do not commit embedding vectors; the cache is a local, regenerable artifact.

## Dependencies

- `text-embedding-3-small` at 1536 dimensions, matching the indexed corpus.
- Existing fast ACL tests, the independent entitlement oracle, and the manual
  `full_acl` test on `main`.

## Interface and data contract

- `knowledge_browser.eval_query_embeddings` reads and writes a cache keyed by a
  hash of the query text, so editing one golden query re-requests only that
  vector and a model change invalidates the file.
- `python -m knowledge_browser.eval_query_embeddings --queries <path> --out
  <path>` builds the cache; `NATIVE_QUERY_EMBEDDINGS` overrides its location.
- `entitlement_classes(memberships, documents, distinguish)` returns one
  representative per class mapped to its members. `distinguish` carries ranking
  inputs that read the user, and the native audit passes each user's
  `primary_project_id` because `released.json` sets `personalization_weight`.
- A gate skips with instructions when embeddings are unavailable. It never
  substitutes a zero vector.

## Safety invariants

- Class reduction is exact only while every user-dependent ranking signal is
  passed through `distinguish`; a new signal that is not passed there silently
  makes the audit approximate.
- The exhaustive matrix, unknown-user default deny, root and child checks, and
  forbidden-result checks remain enabled.
- Selecting more ACL questions can only widen the fast sample.

## Quality and performance

- The entitlement-class audit runs 4 x 298 = 1,192 pairs in about 2.5 minutes
  against the loaded corpus, so nightly carries it.
- The manual matrix keeps its full cost and its existing assertions.
- Building the cache is one provider round trip for 274 distinct query texts.

## Acceptance criteria

- Neither native gate passes a zero vector.
- The entitlement-class audit reports classes whose members sum to every user,
  and zero root and child leaks.
- The fast ACL sample contains every golden query naming a forbidden document.
- The fixture corpus carries ten permission-set signatures against the native
  corpus's four: a set naming two groups, a direct grant layered on a group
  grant, two named users on a set that already reaches one of them through its
  group, a set whose only group has no members, a set no one is granted, one
  set governing two documents, a group that grants nothing, a user in two
  granting groups, and an unknown user.
- The fixture golden set carries one whole-corpus sweep per entitlement class,
  plus one for the redundant grant that must collapse into an existing class.
  Each names the exact documents that user may read and every document that
  must stay hidden.

## Verification

- Strict pytest marker collection.
- Unit, integration, and evaluation tiers.
- `test_native_corpus_entitlement_classes_have_no_acl_leaks` against the loaded
  native database.

## Implementation inputs

- `api/src/knowledge_browser/eval_query_embeddings.py`
- `api/src/knowledge_browser/eval_entitlement.py`
- `api/src/knowledge_browser/eval_loop.py`
- `api/tests/test_native_acl.py`
- `api/tests/test_native_retrieval.py`
- `api/tests/test_eval_integration.py`
- `api/tests/test_eval_loop_integration.py`
- `api/tests/conftest.py`
- `eval/fixture_queries.json`
- `README.md`
- `.gitignore`
