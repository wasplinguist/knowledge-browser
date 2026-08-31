# Repository rules

## Contract-first features

Every product feature starts with a contract based on
`docs/contracts/FEATURE_CONTRACT_TEMPLATE.md`. One feature is one branch, one
worktree, and one pull request. Pull requests are squash-merged into `main`.

Do not copy old experiments, caches, generated reports, local settings, or
inactive datasets into this repository. The old `knowledge-search` repository
is reference material only.

## Active product order

Build against the populated existing database in this order: existing database
compatibility and ACL-safe reads, hybrid retrieval, search API, grounded RAG,
then the web experience. Canonical synthetic dataset generation and source
ingestion are deferred; they are not dependencies for the active product path.

## Search-changing work

Before changing search behavior, read `docs/PRODUCT_INTENT.md` and run its
intent checklist. Search quality is the first priority. Preserve ACL safety and
acceptable latency. Fixture-only improvements are not proof against real
native-service search.

Use `docs/agents/intent-auditor.md` before implementing a search experiment.
Do not proceed without user direction when it returns `UNCLEAR` or `DRIFT`.

When the user asks to run the eval-driven development loop or improve search
from recent behavior, read and follow
`.codex/skills/eval-driven-development/SKILL.md`. A real loop needs a new
challenger and a new eval; an old run cannot complete it.

## Worktrees and merging

- Create each task branch from the latest remote `main`.
- Use one branch in only one worktree.
- Use separate test database names for parallel worktrees.
- Commit the contract, tests, and implementation as reviewable steps.
- Push the task branch and open one focused pull request.
- Squash-merge the pull request, then remove its branch and worktree.
- Rebase or recreate dependent work only after its dependency reaches `main`.
