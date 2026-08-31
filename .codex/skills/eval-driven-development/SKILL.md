---
name: eval-driven-development
description: Use when improving search from recent user behavior or when asked to run the eval-driven development loop.
---

# Eval-Driven Development

Complete this chain with fresh evidence at every step:

```text
Behavior → Insight → Hypothesis → New challenger → New eval → Compare → Decision
```

## Start

Read `docs/PRODUCT_INTENT.md`, `docs/agents/search-intent-analyzer.md`,
`docs/agents/intent-auditor.md`, and
`docs/contracts/eval-driven-development-loop.md`.

Create fresh local evidence outside the repository:

```bash
DATABASE_URL='postgresql://...' api/.venv/bin/python scripts/run_eval_loop.py \
  analyze --days 7 --output-dir /tmp/knowledge-browser-behavior
```

If there is no useful fresh behavior, stop. Do not invent an experiment from
fixtures. Analyze one repeated failure or reformulation. Record one measurable
hypothesis, target metric, regression risk, and golden-set gaming risk. Apply
the intent auditor. `UNCLEAR` or `DRIFT` stops and needs user direction.

## Develop and evaluate

Use test-driven development to create one genuinely different challenger.
Change golden labels only when the behavior evidence proves a missing or wrong
label. Record the reason either way in the experiment manifest.

Follow the manifest contract, then run:

```bash
DATABASE_URL='postgresql://...' api/.venv/bin/python scripts/run_eval_loop.py \
  evaluate --experiment eval/experiments/<id>/experiment.json \
  --output-dir /tmp/knowledge-browser-runs/<id>
```

The output directory must be new. The runner checks input hashes, runs both
profiles on the same queries and embeddings, performs a deterministic fast ACL
sample, and creates `run.json` plus `report.html`.

Use the exact manifest keys and file rules in the contract's **Manifest
example**. Focused development checks are:

```bash
api/.venv/bin/python -m pytest -q -m "unit or integration" api/tests
api/.venv/bin/python -m pytest -q -m "(search_eval or rag_eval) and not nightly" api/tests
```

## Decide

- `reject`: quality gate fails or any fast ACL/forbidden leak appears.
- `recommend-release-gate`: development gates pass.

A recommendation is not a release. A human reviews the fresh report, then runs
the separate native `full_acl` gate with `python -m pytest -q -m full_acl`
from `api/`. Never edit `released.json` automatically. Do not run that native
gate during ordinary challenger development.

## Common mistakes

| Mistake | Required response |
| --- | --- |
| Reuse yesterday's run | Run again into a new empty directory. |
| Challenger equals released | Implement a real candidate first. |
| Tiny ACL fixture passed | Treat it as smoke only; fast sample is development proof and native full ACL is release proof. |
| Golden case added to reward the idea | Remove it unless behavior evidence supports the label. |
| Good average hides losses | Read per-query wins and losses before recommending the release gate. |

Report the evidence path, insight, hypothesis, changed files, golden decision,
run/report paths, hashes, metrics, wins/losses, fast ACL pairs/leaks, and final
decision. If one link is missing, call the loop incomplete.
