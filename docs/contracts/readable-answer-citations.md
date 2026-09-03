# Feature contract: Readable answer citations

## Status

Implemented

## User outcome

Grounded answers render as readable Markdown and refer to supporting documents
with compact numeric markers such as `[1]`, `[2]`, and `[3]`. Internal chunk
identifiers never appear in the visible answer.

## Evidence

A generated answer displayed an internal marker such as
`[dsid_...:body:0]` even though the Sources section already numbered that
document. The answer was also formatted as a dense paragraph despite the web
client supporting Markdown.

## Scope

- Ask the answer model for concise, well-spaced Markdown with selective bold
  emphasis and lists where they improve scanning.
- Normalize bracketed citation IDs and accepted citation aliases to the numeric
  order of the validated citations returned by the API.
- Add focused regression coverage for raw citation IDs and formatting guidance.

## Non-goals

- Change retrieval, ranking, ACL filtering, or document-opening behavior.
- Add a new Markdown renderer or permit raw HTML in generated answers.
- Change the citation response schema or source-panel interaction.

## Dependencies

The existing grounded-answer API, validated citation metadata, React Markdown
renderer, and interactive Sources panel on `main`.

## Interface and data contract

The `/api/answer` response shape is unchanged. `answer` remains a Markdown
string and `citations` remains an ordered array of validated citation objects.
When the model writes `[<chunk_id>]`, `[<external_id>]`, or `[<source_url>]`
for a citation present in that array, the server returns `[n]`, where `n` is
the citation's one-based position. Existing numeric markers remain unchanged.

## Safety invariants

- Only opened, ACL-allowed chunks can become response citations.
- Citation normalization cannot introduce or expose a source absent from the
  validated citation array.
- Raw HTML remains disabled in the web renderer, and remote answer images are
  not loaded.

## Quality and performance

The released grounding behavior and response schema remain the baseline.
Normalization is one bounded regular-expression pass over the answer and adds
no model calls, searches, reads, or meaningful latency.

## Acceptance criteria

- A raw bracketed chunk ID for the first validated source renders as `[1]`.
- Multiple validated source IDs render according to citation-array order.
- Normal Markdown links and already numeric citation markers are preserved.
- Model instructions explicitly request readable Markdown, whitespace, lists,
  selective bold emphasis, and numeric inline citations.
- Existing safe-Markdown and interactive-citation tests continue to pass.

## Verification

- Run the focused API answer unit tests.
- Run the web test suite and production build.
- Review the resulting diff for response-schema and ACL invariants.

## Implementation inputs

- `api/src/knowledge_browser/answer.py`
- `api/tests/test_answer.py`
- `web/src/App.tsx`
- `web/src/App.test.tsx`
- `docs/contracts/FEATURE_CONTRACT_TEMPLATE.md`
