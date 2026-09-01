from pathlib import Path
from uuid import UUID

import pytest

from knowledge_browser.dataset import load_dataset
from knowledge_browser.embedding_index import collect_sentences
from knowledge_browser.importer import import_dataset
from knowledge_browser.repository import get_document


DATA = Path(__file__).parents[2] / "data" / "company"
pytestmark = pytest.mark.integration


def _vectors(dataset):
    vector = [0.0] * 1536
    return {text: vector for text in collect_sentences(dataset.documents)}


def _user_id(db, email):
    return db.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()[0]


def test_imports_expected_counts(db):
    db.execute("TRUNCATE documents, users, groups, permission_sets CASCADE")
    dataset = load_dataset(DATA)

    report = import_dataset(
        db, dataset, _vectors(dataset), model="text-embedding-3-small"
    )

    assert (report.users, report.documents, report.chunks, report.sentences) == (
        100,
        1000,
        13145,
        16520,
    )


def test_missing_embedding_rolls_back(db):
    db.execute("TRUNCATE documents, users, groups, permission_sets CASCADE")

    with pytest.raises(ValueError, match="missing embedding"):
        import_dataset(db, load_dataset(DATA), {}, model="text-embedding-3-small")

    assert db.execute("SELECT count(*) FROM documents").fetchone() == (0,)


def test_imported_acl_allows_company_team_and_direct_users_only(db):
    db.execute("TRUNCATE documents, users, groups, permission_sets CASCADE")
    dataset = load_dataset(DATA)
    import_dataset(db, dataset, _vectors(dataset), model="text-embedding-3-small")

    product_platform_user = _user_id(db, "sofia.brooks@copperline.example")
    direct_user = _user_id(db, "aisha.park@copperline.example")
    unrelated_user = _user_id(db, "mateo.martin@copperline.example")
    unknown_user = UUID("00000000-0000-0000-0000-000000000099")

    assert get_document(
        db, product_platform_user, "slack", "artifact-001-slack-report"
    ) is not None
    assert get_document(
        db, product_platform_user, "slack", "artifact-001-slack-summary"
    ) is not None
    assert get_document(
        db, direct_user, "slack", "artifact-005-slack-report"
    ) is not None
    assert get_document(
        db, unrelated_user, "slack", "artifact-001-slack-summary"
    ) is None
    assert get_document(
        db, unknown_user, "slack", "artifact-001-slack-report"
    ) is None
