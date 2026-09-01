import json
from pathlib import Path
import re

import pytest

from knowledge_browser.profiles import expand_query, load_profile


pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]
CATALOG = ROOT / "data" / "company" / "projects.jsonl"
CANDIDATE = ROOT / "search" / "profiles" / "candidates" / "project-aliases-v1.json"


def _catalog_expansions() -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    spellings: dict[tuple[str, str], set[str]] = {}
    for line in CATALOG.read_text(encoding="utf-8").splitlines():
        project = json.loads(line)
        canonical = project["name"]
        aliases = {
            *project["aliases"],
            project["jira_key"],
            project["repository"],
            *(alias for values in project["aliases_by_source"].values() for alias in values),
        }
        for alias in aliases:
            if re.search(
                rf"(?<!\w){re.escape(alias)}(?!\w)", canonical, re.IGNORECASE
            ):
                continue
            folded = alias.casefold()
            candidates.setdefault(folded, set()).add(canonical)
            spellings.setdefault((folded, canonical), set()).add(alias)
    return {
        alias: canonical
        for folded, canonicals in candidates.items()
        if len(canonicals) == 1
        for canonical in canonicals
        for alias in spellings[(folded, canonical)]
    }


def test_candidate_contains_every_unambiguous_catalog_alias():
    profile = load_profile(CANDIDATE)

    assert profile.name == "project-aliases-v1"
    assert profile.query_expansions == _catalog_expansions()


def test_candidate_does_not_reexpand_canonical_project_names():
    profile = load_profile(CANDIDATE)

    for line in CATALOG.read_text(encoding="utf-8").splitlines():
        canonical = json.loads(line)["name"]
        assert expand_query(canonical, profile) == canonical


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("NIMREL incident", "Nimbus Relay incident"),
        ("nimbus-relay incident", "Nimbus Relay incident"),
        ("Nimbus Relay Program incident", "Nimbus Relay incident"),
        ("copperline/nimbus-relay incident", "Nimbus Relay incident"),
        ("PR review", "Prism Rules review"),
        ("pr review", "pr review"),
        ("PR-401 status", "PR-401 status"),
        ("NIMRELATION status", "NIMRELATION status"),
    ],
)
def test_candidate_expands_only_complete_alias_terms(query: str, expected: str):
    profile = load_profile(CANDIDATE)

    assert expand_query(query, profile) == expected
