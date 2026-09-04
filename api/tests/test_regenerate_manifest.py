from pathlib import Path

import pytest

from scripts.regenerate_manifest import build_manifest, render


DATASET = Path(__file__).parents[2] / "data" / "redwood"
pytestmark = pytest.mark.unit


def test_committed_manifest_matches_the_dataset_it_describes():
    """A dataset edit that skipped regeneration fails here, not at import time."""
    with open(DATASET / "manifest.json", encoding="utf-8", newline="") as handle:
        committed = handle.read()

    assert render(build_manifest(DATASET)) == committed


def test_rendered_manifest_carries_no_carriage_returns():
    """One CRLF changes the digest of every file the manifest lists."""
    assert "\r" not in render(build_manifest(DATASET))
