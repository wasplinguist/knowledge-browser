from pathlib import Path

import pytest

from knowledge_browser.dataset import validate_streaming_dataset
from knowledge_browser.bulk_writer import (
    ensure_permissions,
    import_identities,
    permission_id,
    stable_uuid,
)


DATA = Path(__file__).parents[2] / "data" / "company"
pytestmark = pytest.mark.integration


@pytest.fixture
def validated_dataset():
    return validate_streaming_dataset(DATA)


def _empty_identities(db):
    db.execute("TRUNCATE users, groups, permission_sets CASCADE")


def test_stable_ids_are_repeatable():
    assert stable_uuid("document", "jira:ABC-1") == stable_uuid(
        "document", "jira:ABC-1"
    )
    assert stable_uuid("document", "jira:ABC-1") != stable_uuid(
        "document", "jira:ABC-2"
    )


def test_identity_import_is_idempotent(db, validated_dataset):
    _empty_identities(db)

    first = import_identities(db, validated_dataset.context, page_size=10)
    second = import_identities(db, validated_dataset.context, page_size=10)

    assert first == second
    assert db.execute("SELECT count(*) FROM users").fetchone() == (len(first.users),)
    expected_memberships = sorted(
        (first.groups[group["name"]], first.users[email])
        for group in validated_dataset.context["identity_groups"]
        for email in group["members"]
    )
    assert db.execute(
        "SELECT group_id, user_id FROM group_memberships ORDER BY group_id, user_id"
    ).fetchall() == expected_memberships


def test_permissions_link_company_group_and_direct_user_exactly(db, validated_dataset):
    _empty_identities(db)
    identities = import_identities(db, validated_dataset.context, page_size=10)
    group_name = next(iter(identities.groups))
    user_email = next(iter(identities.users))
    default_acl = None
    company_acl = {"company": True}
    group_acl = {"groups": [group_name]}
    direct_acl = {"users": [user_email]}

    acls = (default_acl, company_acl, group_acl, direct_acl)
    ensure_permissions(db, acls, identities)
    ensure_permissions(db, acls, identities)

    assert db.execute("SELECT count(*) FROM permission_sets").fetchone() == (4,)
    assert db.execute(
        "SELECT visibility FROM permission_sets WHERE id = %s",
        (permission_id(default_acl),),
    ).fetchone() == ("restricted",)
    assert db.execute(
        "SELECT visibility FROM permission_sets WHERE id = %s",
        (permission_id(company_acl),),
    ).fetchone() == ("company",)
    assert db.execute(
        "SELECT count(*) FROM permission_set_users WHERE permission_set_id = %s",
        (permission_id(default_acl),),
    ).fetchone() == (0,)
    assert db.execute(
        "SELECT count(*) FROM permission_set_groups WHERE permission_set_id = %s",
        (permission_id(default_acl),),
    ).fetchone() == (0,)
    assert db.execute(
        "SELECT count(*) FROM permission_set_users WHERE permission_set_id = %s",
        (permission_id(company_acl),),
    ).fetchone() == (0,)
    assert db.execute(
        "SELECT count(*) FROM permission_set_groups WHERE permission_set_id = %s",
        (permission_id(company_acl),),
    ).fetchone() == (0,)
    assert db.execute(
        "SELECT user_id FROM permission_set_users WHERE permission_set_id = %s",
        (permission_id(direct_acl),),
    ).fetchall() == [(identities.users[user_email],)]
    assert db.execute(
        "SELECT count(*) FROM permission_set_groups WHERE permission_set_id = %s",
        (permission_id(direct_acl),),
    ).fetchone() == (0,)
    assert db.execute(
        "SELECT group_id FROM permission_set_groups WHERE permission_set_id = %s",
        (permission_id(group_acl),),
    ).fetchall() == [(identities.groups[group_name],)]
    assert db.execute(
        "SELECT count(*) FROM permission_set_users WHERE permission_set_id = %s",
        (permission_id(group_acl),),
    ).fetchone() == (0,)
