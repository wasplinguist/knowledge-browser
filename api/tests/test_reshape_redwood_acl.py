import json
from pathlib import Path

import pytest

from knowledge_browser.dataset import load_dataset
from scripts.reshape_redwood_acl import rewrite, select, signatures


DATASET = Path(__file__).parents[2] / "data" / "redwood"
pytestmark = pytest.mark.unit

COMPANY = {"company_access": True, "group_ids": [], "user_ids": []}


def _acl(groups=(), users=()):
    return {
        "company_access": False,
        "group_ids": list(groups),
        "user_ids": list(users),
    }


def _doc(identifier, acl=None):
    return {"id": identifier, "acl": dict(acl or COMPANY), "payload": {}}


def _corpus(count=40):
    return [_doc(f"dsid_{index:04d}") for index in range(count)]


def _plan(count=3):
    return [(f"shape {index}", _acl(groups=[f"group-{index}"])) for index in range(count)]


def _apply(records, chosen):
    for index, acl in chosen:
        records[index]["acl"] = dict(acl)


def test_selection_never_touches_a_golden_document():
    records = _corpus()
    reserved = {"dsid_0000", "dsid_0001", "dsid_0002"}

    chosen = select(records, reserved, _plan(), per_signature=2)

    assert len(chosen) == 6
    assert not reserved & {records[index]["id"] for index, _ in chosen}


def test_selection_leaves_already_restricted_documents_alone():
    records = _corpus()
    records[5]["acl"] = _acl(groups=["group-security"])

    chosen = select(records, set(), _plan(), per_signature=2)

    assert len(chosen) == 6
    assert 5 not in {index for index, _ in chosen}


def test_selection_repeats_itself_after_its_own_edits():
    """Without this, a second run restricts a fresh set of documents."""
    records = _corpus()
    first = select(records, set(), _plan(), per_signature=2)
    _apply(records, first)

    assert len(first) == 6
    assert select(records, set(), _plan(), per_signature=2) == first


def test_every_signature_governs_more_than_one_document():
    records = _corpus()

    chosen = select(records, set(), _plan(), per_signature=2)

    counts = {}
    for _, acl in chosen:
        counts[json.dumps(acl, sort_keys=True)] = counts.get(json.dumps(acl, sort_keys=True), 0) + 1
    assert set(counts.values()) == {2}


def test_selection_spreads_across_the_corpus():
    """Confluence records cluster by space, so consecutive picks restrict one topic."""
    records = _corpus(count=400)

    indexes = sorted(index for index, _ in select(records, set(), _plan(), per_signature=2))

    assert max(indexes) - min(indexes) > len(records) // 2


def test_direct_grants_name_users_outside_the_granted_groups():
    members = {
        "group-all-employees": ["emp-1", "emp-2", "emp-3", "emp-4", "emp-5", "emp-6",
                                "emp-7", "emp-8", "emp-9"],
        "group-security": ["emp-1"],
        "group-platform": ["emp-2"],
        "group-infrastructure": ["emp-3"],
        "group-product": ["emp-4"],
        "group-revenue": ["emp-5"],
        "group-workplace": ["emp-6"],
        "group-private-deployments": ["emp-7"],
    }

    outside = [acl for label, acl in signatures(members)
               if label == "group plus outside direct grant"]

    assert outside, "expected a group-plus-outside-direct-grant signature"
    assert outside[0]["user_ids"]
    assert set(outside[0]["user_ids"]).isdisjoint(members["group-infrastructure"])


def test_redundant_grant_names_a_user_already_in_the_granted_group():
    members = {
        "group-all-employees": ["emp-1", "emp-2", "emp-3", "emp-4", "emp-5", "emp-6",
                                "emp-7", "emp-8", "emp-9"],
        "group-security": ["emp-1"],
        "group-platform": ["emp-2"],
        "group-infrastructure": ["emp-3"],
        "group-product": ["emp-4"],
        "group-revenue": ["emp-5"],
        "group-workplace": ["emp-6"],
        "group-private-deployments": ["emp-7"],
    }

    redundant = [acl for label, acl in signatures(members)
                 if label == "group plus redundant direct grant"]

    assert redundant, "expected a redundant-grant signature"
    assert set(redundant[0]["user_ids"]) <= set(members["group-security"])


def test_rewrite_keeps_untouched_lines_byte_for_byte_and_writes_lf(tmp_path: Path):
    source = tmp_path / "artifacts" / "confluence.jsonl"
    source.parent.mkdir(parents=True)
    original = [
        json.dumps(_doc(f"dsid_{index:04d}"), sort_keys=True, ensure_ascii=False)
        for index in range(40)
    ]
    with open(source, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(original) + "\n")

    changed = rewrite(source, reserved=set(), plan=_plan(), per_signature=2, apply=True)

    with open(source, encoding="utf-8", newline="") as handle:
        written = handle.read()
    lines = written.splitlines()
    touched = {index for index, line in enumerate(lines) if line != original[index]}
    assert changed == 6
    assert len(touched) == 6
    assert "\r" not in written
    assert all(lines[index] == original[index]
               for index in range(len(original)) if index not in touched)


def test_committed_corpus_carries_the_reshaped_signatures():
    """Reverting the reshaped ACLs drops this back to the original four."""
    documents = load_dataset(DATASET).documents

    shapes = {
        (
            (acl := document.acl or {}).get("company", False),
            tuple(sorted(acl.get("groups", []))),
            len(acl.get("users", [])),
        )
        for document in documents
    }
    direct = sum(1 for document in documents if (document.acl or {}).get("users"))

    assert len(shapes) == 12
    assert direct == 8, "permission_set_users stays empty without direct grants"
