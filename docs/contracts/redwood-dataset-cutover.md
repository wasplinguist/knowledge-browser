# Feature contract: Redwood dataset cutover

## Status

Approved by the user on 2026-09-02.

## User outcome

Knowledge Browser starts, tests, and evaluates against one manifest-verified
Redwood corpus. The previous company corpus and evaluation suite are no longer
active or committed.

## Evidence

- The approved Redwood manifest contains 13,214 artifacts: 1,904 Confluence,
  3,825 GitHub, 3,303 Jira, and 4,182 Slack records.
- A clean import completed in the isolated `knowledge_redwood` database with
  13,214 documents, 398,919 chunks, and 1,062,078 embedded sentences.
- `eval/redwood_queries.json` contains 298 questions for the replacement
  golden set.

## Scope

- Replace `data/company/**` with `data/redwood/**` as the only committed
  bootstrap corpus.
- Replace `eval/queries.json` with `eval/redwood_queries.json` as the complete
  golden set.
- Update bootstrap paths, repository policy, tests, documentation, and SVG
  diagrams for the Redwood corpus.
- Run the normal test tiers and report evaluation metrics by question family
  against the populated Redwood database.

## Non-goals

- Do not change ranking, retrieval, ACL, or answer-generation behavior.
- Do not add dataset generation, connectors, or scheduled ingestion.
- Do not commit evaluation reports, caches, credentials, local settings, or
  inactive datasets.

## Dependencies

- Safe resumable Redwood import and verification on `main`.
- Existing portable bootstrap, hybrid retrieval, evaluation runner, and
  released search profile.

## Interface and data contract

- `data/redwood/manifest.json` is the committed corpus authority.
- `scripts/setup_database.sh` imports `data/redwood` for an empty portable
  database and verifies an already-populated database on later starts.
- `eval/redwood_queries.json` is the complete golden query path used by
  documentation, integration tests, and evaluation-loop configuration.
- Generated evaluation output remains outside Git.

## Safety invariants

- ACL source records retain company, group, and direct-user semantics.
- Evaluation uses the existing ACL-filtered search path and must report no
  unauthorized evidence.
- Dataset validation runs before bootstrap writes.
- No credentials, document bodies, embeddings, or user-private answers are
  written to reports or logs.

## Quality and performance

- This cutover changes inputs and references, not search behavior.
- Normal API, web, Compose, marker-audit, and evaluation checks must pass.
- The evaluation report must include each question family's question count and
  supported retrieval metrics rather than only a corpus-wide aggregate.
- Search latency remains subject to the released profile and existing gates.

## Acceptance criteria

- Git tracks `data/redwood/**` and does not track `data/company/**`,
  `eval/queries.json`, or any `docs/superpowers/**` file.
- The committed manifest validates with exactly 13,214 artifacts from all four
  sources.
- Portable bootstrap and dataset tests resolve `data/redwood`.
- The 298-question Redwood golden set loads and evaluates against the populated
  Redwood database.
- README text and diagrams describe only paths and counts that exist in the
  repository.
- Per-question-family evaluation metrics are recorded in the PR and final
  handoff, not committed as a generated report.

## Verification

- Dataset validation, importer, bootstrap, alias-catalog, and setup-script
  tests against `data/redwood`.
- Full normal API test tier excluding exhaustive ACL, retrieval, and nightly
  markers.
- Web tests and production build.
- Compose validation, marker audit, and `git diff --check`.
- Redwood evaluation against `knowledge_redwood`, grouped by question family.
- Manual tracked-file audit for inactive datasets, reports, and
  `docs/superpowers`.

## Implementation inputs

- `docs/contracts/FEATURE_CONTRACT_TEMPLATE.md`
- `data/redwood/**`
- `eval/redwood_queries.json`
- `scripts/setup_database.sh`
- `api/src/knowledge_browser/dataset.py`
- `api/src/knowledge_browser/evaluation.py`
- `README.md` and `docs/images/*.svg`
