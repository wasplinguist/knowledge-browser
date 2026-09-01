# Feature contract: Markdown AI answer

## Status

Approved by the user on 2026-09-01.

## User outcome

AI answer text displays normal Markdown formatting instead of showing Markdown
characters as plain text. Headings, emphasis, lists, links, and code are easy to
read while evidence citations remain interactive.

## Evidence

The answer view currently splits text into paragraphs and renders plain text.
An answer containing Markdown such as `**Jira**`, headings, or lists does not
receive Markdown formatting.

## Scope

- Render the main AI answer with standard Markdown.
- Keep numbered evidence citations clickable in the local document panel.
- Style Markdown blocks inside the existing answer card.
- Add focused rendering and citation tests.

## Non-goals

- Do not change answer generation, search behavior, Sources cards, Ask next
  buttons, missing-information blocks, or document details.
- Do not allow raw HTML from model output.
- Do not add Markdown editing or preview controls.

## Dependencies

- Grounded RAG answers and grounded answer provenance.
- The existing React web experience.
- `react-markdown` for safe Markdown parsing and React rendering.

## Interface and data contract

The `POST /api/answer` response is unchanged. `answer` remains a string.
Markdown syntax in that string is rendered in the browser. Plain citation
markers such as `[1]` are mapped to the existing citation button behavior.

## Safety invariants

- Raw HTML is not parsed or inserted into the DOM.
- Markdown links use safe browser attributes and do not replace citation
  buttons.
- Citation numbers only open ACL-allowed citation objects already returned by
  the answer API.
- No answer text or document data is added to browser logs.

## Quality and performance

The visible result must preserve the existing answer layout and citation
interaction. Markdown parsing happens only for one answer string. The web
production build must remain successful without new warnings from application
code.

## Acceptance criteria

- Headings, bold text, lists, links, and inline code render as HTML elements.
- Raw HTML from the answer is not rendered as active HTML.
- `[1]` remains a keyboard-accessible button that opens citation 1.
- Plain-text answers still display normally.
- Sources and Ask next keep their current behavior.
- Empty and unavailable answers keep their current behavior.

## Verification

- Focused web component tests for Markdown and inline citations.
- Full web test suite.
- Web production build.
- Visual check in the local browser.
- `git diff --check`.

## Source reference

No old repository files or generated artifacts are used.
