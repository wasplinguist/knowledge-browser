import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit
ROOT = Path(__file__).parents[2]


def test_redwood_is_the_only_committed_product_dataset():
    manifest = json.loads(
        (ROOT / "data" / "redwood" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["counts"]["artifacts"] == 13_214
    assert not (ROOT / "data" / "company").exists()


def test_redwood_queries_are_the_complete_golden_set():
    queries = json.loads(
        (ROOT / "eval" / "redwood_queries.json").read_text(encoding="utf-8")
    )

    assert len(queries) == 298
    assert not (ROOT / "eval" / "queries.json").exists()
