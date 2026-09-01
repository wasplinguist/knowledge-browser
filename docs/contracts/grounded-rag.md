# Feature contract: grounded RAG answers

## Status

Implemented.

## User outcome

An allowed user can ask a company question and receive an answer grounded only
in ACL-safe evidence that the answer loop opened during that request.

## Scope

- `POST /api/answer` with `auto`, `fast`, and `deep` modes.
- One bounded answer loop using Feature 3 hybrid retrieval first.
- ACL-safe full-chunk reads for evidence.
- Request-local evidence tracking and validated citation objects.
- Complete, incomplete, and conflicting evidence states.
- Deterministic auto routing and separate fast/deep tool/read budgets.
- Safe execution counters and optional metadata-only debug trace.
- Provider injection for deterministic tests and an optional OpenAI-compatible
  Responses client at runtime.

## Non-goals

- UI, ranking experiments, eval reports, persistent traces, ingestion, or paid
  model calls in tests.
- Model-judged answer quality or automatic release decisions.
- Exposing prompts, chain of thought, raw tool payloads, or document text in
  telemetry.

## Safety and behavior

- The first discovery tool is the same `hybrid_search` used by result lists.
- Only full chunks returned by an ACL-safe read may become citations.
- Unknown, snippet-only, duplicate, or ambiguous citation IDs are removed.
- A claimed complete answer without a valid citation becomes incomplete.
- Valid conflicts set the answer state to conflicting.
- Denied evidence behaves as not found.
- Tool and read budgets are enforced by the server.
- Provider, tool, or parsing failures return a small safe answer error and the
  execution work already recorded.

## Acceptance criteria

- Router, shared retrieval, citation membership, ACL denial, conflict, budget,
  invalid JSON, and API validation tests pass.
- No test makes a network request.
- Existing search and ACL tests remain green.
- The focused suite finishes in under one minute locally.

## Verification

- `api/.venv/bin/python -m pytest -q -m "unit or integration" api/tests`
- `npm test -- --run` from `web/`
- `npm run build` from `web/`
- `docker compose config --quiet`
- `git diff --check`
