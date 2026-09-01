# Feature contract: web experience

## Status

Implemented.

## User outcome

An allowed demo user can search company knowledge, read a grounded AI answer,
and open stored Jira, Confluence, Slack, and GitHub content without leaving the
search page or following broken demo URLs.

## Scope

- Load demo users and require one selected demo identity for search and answers.
- Show search progress without briefly showing an empty-result page.
- Show result facets and one grouped result per document from the existing API.
- Start the AI answer only after search succeeds and keep answer errors separate
  from usable search results.
- Show only one provenance item for repeated citations to the same document.
- Record result clicks and open an in-page source detail panel.
- Add `GET /api/documents/{source}/{external_id}` for ACL-safe display data.
- Render small read-only Jira, Confluence, Slack, and GitHub views.
- Remove unfinished time, author, type, and history controls.

## Non-goals

- Real login, editing, comments, source-service navigation, URL routing, or exact
  copies of source products.
- New search, ranking, answer, ingestion, or database-schema behavior.
- A new UI framework, router, state library, or paid provider call.

## Dependencies

- Existing database compatibility and ACL-safe reads.
- Hybrid retrieval.
- Search API and analytics.
- Grounded RAG answers.

## Interface and data contract

`GET /api/documents/{source}/{external_id}` requires `X-Demo-User-Id` and
returns common document display fields plus a source-specific `payload`. The
payload uses an allowlist for each source and omits ACLs, evaluation truth,
event links, noise labels, and unrelated raw metadata.

Missing and forbidden documents both return `404 document_not_found`. An
invalid source returns `400 invalid_source`. Database failures return the safe
`503 document_unavailable` envelope.

The web app uses the existing search, answer, click, and demo-user endpoints.
It does not navigate to stored external URLs.

## Safety invariants

- A detail read uses the same document-and-root ACL rule as search and chunk
  reads.
- A malformed root chain is not readable.
- Missing ACL data fails closed.
- Forbidden and missing document identities are indistinguishable.
- Raw ACL, synthetic truth, and private metadata never reach the browser.

## Quality and performance

- This feature does not change search behavior or ranking.
- Search progress appears immediately; empty results appear only after the
  request finishes.
- Search and document reads keep existing latency behavior. The UI adds no
  sequential request before showing result items.
- Focused API and web tests should finish in under one minute locally.

## Acceptance criteria

- Editing or submitting a new query clears stale answer and result state.
- A loading search never shows `No results found`.
- A completed empty search does show `No results found`.
- Repeated citations for one URL or source/external ID appear once.
- Clicking a result records the click, keeps search results visible, and opens
  the source detail panel.
- The panel has dialog semantics, loading and safe-error states, Escape and
  backdrop closing, and focus return to the clicked result.
- Late responses cannot replace a newer panel or a closed panel.
- Main display fields for all four supported sources render.
- No unfinished filter or history buttons appear.

## Verification

- `api/.venv/bin/python -m pytest -q -m "unit or integration" api/tests`
- `npm test -- --run` from `web/`
- `npm run build` from `web/`
- `docker compose config --quiet`
- `git diff --check`
- One final whole-feature review before the pull request.
