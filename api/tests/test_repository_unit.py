from uuid import UUID

import pytest

from knowledge_browser.repository import resolve_identity


pytestmark = pytest.mark.unit


class RecordingConnection:
    def __init__(self):
        self.query = ""
        self.parameters = {}

    def execute(self, query, parameters):
        self.query = query
        self.parameters = parameters
        return self

    def fetchone(self):
        return None


def test_uuid_identity_uses_the_uuid_indexable_column_without_a_cast():
    conn = RecordingConnection()
    identity_id = UUID("00000000-0000-0000-0000-000000000003")

    resolve_identity(conn, str(identity_id))

    assert "WHERE id = %(identity_id)s" in conn.query
    assert "id::text" not in conn.query
    assert conn.parameters == {"identity_id": identity_id}


@pytest.mark.parametrize(
    "identity", ["group@example.test", "00000000-0000-0000-0000-not-a-uuid"]
)
def test_non_uuid_identity_uses_the_email_indexable_column(identity):
    conn = RecordingConnection()

    resolve_identity(conn, identity)

    assert "WHERE email = %(email)s" in conn.query
    assert " OR " not in conn.query
    assert conn.parameters == {"email": identity}
