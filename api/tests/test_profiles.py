import json
from pathlib import Path

import pytest

from knowledge_browser.profiles import SearchProfile, expand_query, load_profile


pytestmark = pytest.mark.unit


def test_profile_rejects_invalid_retrieval_settings():
    with pytest.raises(ValueError, match="profile name"):
        SearchProfile(name="")
    with pytest.raises(ValueError, match="keyword_limit"):
        SearchProfile(name="bad", keyword_limit=0)
    with pytest.raises(ValueError, match="retrieval weight"):
        SearchProfile(name="bad", keyword_weight=0, semantic_weight=0)


def test_profile_loads_from_json(tmp_path):
    path = tmp_path / "released.json"
    path.write_text(json.dumps({
        "name": "released",
        "keyword_limit": 12,
        "semantic_limit": 8,
        "rrf_k": 40,
        "query_expansions": {"NIMREL": "Nimbus Relay"},
    }))

    profile = load_profile(path)

    assert profile.name == "released"
    assert profile.keyword_limit == 12
    assert profile.semantic_limit == 8
    assert profile.rrf_k == 40


def test_alias_expansion_uses_whole_terms_and_preserves_issue_keys():
    profile = SearchProfile(
        name="aliases",
        query_expansions={"NIMREL": "Nimbus Relay", "db": "database"},
    )

    assert expand_query("NIMREL db problem", profile) == "Nimbus Relay database problem"
    assert expand_query("NIMREL-401 status", profile) == "NIMREL-401 status"
    assert expand_query("NIMRELATION dbx", profile) == "NIMRELATION dbx"


def test_released_profile_is_loadable():
    path = Path(__file__).parents[2] / "search" / "profiles" / "released.json"

    profile = load_profile(path)

    assert profile == SearchProfile(name="released")
