from pathlib import Path
import os

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest


DEFAULT_TEST_DATABASE_URL = (
    "postgresql://postgres:postgres@localhost:5432/knowledge_browser_compat_test"
)
PRIMARY_MARKERS = {"unit", "integration", "search_eval", "rag_eval"}


def pytest_collection_modifyitems(items):
    invalid = []
    for item in items:
        primary = {
            marker.name for marker in item.iter_markers()
            if marker.name in PRIMARY_MARKERS
        }
        if len(primary) != 1:
            invalid.append(f"{item.nodeid}: {sorted(primary) or ['missing']}")
    if invalid:
        raise pytest.UsageError(
            "each test needs exactly one primary marker:\n" + "\n".join(invalid)
        )


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    database = conninfo_to_dict(url).get("dbname", "")
    if "_test" not in database.lower():
        raise RuntimeError("TEST_DATABASE_URL must name a dedicated _test database")
    return url


@pytest.fixture(scope="session")
def prepared_test_database() -> str:
    return _prepare_test_database()


def _prepare_test_database() -> str:
    url = _test_database_url()
    info = conninfo_to_dict(url)
    database = info["dbname"]
    admin_info = {**info, "dbname": "postgres"}

    with psycopg.connect(**admin_info, autocommit=True) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        ).fetchone()
        if not exists:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

    schemas = (
        Path(__file__).parents[2] / "db" / "init" / "001_schema.sql",
        Path(__file__).parents[2] / "db" / "init" / "002_bulk_import.sql",
    )
    with psycopg.connect(url) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        for schema in schemas:
            conn.execute(schema.read_text())
    return url


@pytest.fixture
def db(prepared_test_database: str):
    with psycopg.connect(prepared_test_database) as conn:
        _seed(conn)
        yield conn
        conn.rollback()


def _seed(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        INSERT INTO users (id, email, name) VALUES
          ('00000000-0000-0000-0000-000000000001', 'company@example.test', 'Company User'),
          ('00000000-0000-0000-0000-000000000002', 'direct@example.test', 'Direct User'),
          ('00000000-0000-0000-0000-000000000003', 'group@example.test', 'Group User'),
          ('00000000-0000-0000-0000-000000000004', 'other@example.test', 'Other User');
        INSERT INTO groups (id, name)
          VALUES ('10000000-0000-0000-0000-000000000001', 'engineering');
        INSERT INTO group_memberships (group_id, user_id)
          VALUES ('10000000-0000-0000-0000-000000000001',
                  '00000000-0000-0000-0000-000000000003');

        INSERT INTO permission_sets (id, visibility) VALUES
          ('20000000-0000-0000-0000-000000000001', 'company'),
          ('20000000-0000-0000-0000-000000000002', 'restricted'),
          ('20000000-0000-0000-0000-000000000003', 'restricted'),
          ('20000000-0000-0000-0000-000000000004', 'restricted'),
          ('20000000-0000-0000-0000-000000000005', 'restricted');
        INSERT INTO permission_set_users (permission_set_id, user_id) VALUES
          ('20000000-0000-0000-0000-000000000002',
           '00000000-0000-0000-0000-000000000002'),
          ('20000000-0000-0000-0000-000000000005',
           '00000000-0000-0000-0000-000000000004');
        INSERT INTO permission_set_groups (permission_set_id, group_id)
          VALUES ('20000000-0000-0000-0000-000000000003',
                  '10000000-0000-0000-0000-000000000001');

        INSERT INTO documents (
          id, source, kind, external_id, root_document_id, permission_set_id,
          title, body, author, url, container, raw_payload,
          source_created_at, source_updated_at, indexed_at
        ) VALUES
          ('30000000-0000-0000-0000-000000000001', 'jira', 'issue', 'COMPANY-1',
           '30000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001',
           'Company document', 'Company body', 'Ada', 'https://example.test/company',
           'Atlas', '{"acl":{"company_access":true,"group_ids":["engineering"],
                     "user_ids":["00000000-0000-0000-0000-000000000001"]},
                     "provenance":{"tenant":"northstar"}}',
           '2026-08-01T01:00:00Z', '2026-08-02T01:00:00Z', '2026-08-03T01:00:00Z'),
          ('30000000-0000-0000-0000-000000000002', 'confluence', 'page', 'DIRECT-1',
           '30000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002',
           'Direct document', 'Direct body', 'Bea', 'https://example.test/direct',
           'Engineering', '{"provenance":{"space":"ENG"}}', now(), now(), now()),
          ('30000000-0000-0000-0000-000000000003', 'slack', 'message', 'GROUP-1',
           '30000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000003',
           'Group document', 'Group body', 'Cal', 'https://example.test/group',
           '#engineering', '{"provenance":{"channel":"engineering"}}', now(), now(), now()),
          ('30000000-0000-0000-0000-000000000004', 'github', 'pull_request', 'MISSING-1',
           '30000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000004',
           'Missing-link document', 'Missing body', 'Dev', 'https://example.test/missing',
           'northstar/browser', '{"provenance":{"repository":"browser"}}', now(), now(), now()),
          ('30000000-0000-0000-0000-000000000005', 'jira', 'issue', 'HIDDEN-ROOT',
           '30000000-0000-0000-0000-000000000005', '20000000-0000-0000-0000-000000000005',
           'Hidden root', 'Hidden root body', null, null, 'Atlas', '{}', now(), now(), now()),
          ('30000000-0000-0000-0000-000000000006', 'jira', 'comment', 'VISIBLE-CHILD',
           '30000000-0000-0000-0000-000000000005', '20000000-0000-0000-0000-000000000001',
           'Visible child', 'Visible child body', null, null, 'Atlas', '{}', now(), now(), now()),
          ('30000000-0000-0000-0000-000000000007', 'jira', 'issue', 'VISIBLE-ROOT',
           '30000000-0000-0000-0000-000000000007', '20000000-0000-0000-0000-000000000001',
           'Visible root', 'Visible root body', null, null, 'Atlas', '{}', now(), now(), now()),
          ('30000000-0000-0000-0000-000000000008', 'jira', 'comment', 'HIDDEN-CHILD',
           '30000000-0000-0000-0000-000000000007', '20000000-0000-0000-0000-000000000005',
           'Hidden child', 'Hidden child body', null, null, 'Atlas', '{}', now(), now(), now());

        INSERT INTO chunks (source, id, document_id, field, text, chunk_index, content_hash, metadata)
        SELECT source, source || ':' || external_id || ':0', id, 'body', body, 0,
               external_id || '-hash', jsonb_build_object('external_id', external_id)
        FROM documents;

        UPDATE documents
        SET parent_document_id = root_document_id
        WHERE external_id IN ('VISIBLE-CHILD', 'HIDDEN-CHILD');

        INSERT INTO sentences (source, chunk_id, sentence_index, sentence, embedding, embedding_model)
        SELECT source, source || ':' || external_id || ':0', 0, body,
               ('[' || array_to_string(array_fill('0'::text, ARRAY[1536]), ',') || ']')::halfvec,
               'test-embedding'
        FROM documents;
        """
    )


def _seed_diverse_acl_shapes(conn: psycopg.Connection) -> None:
    """Permission-set signatures the Redwood corpus never produces.

    Every native permission set names exactly one group and no direct user, so
    the corpus alone can never tell a union from an intersection, nor prove that
    a grant reaching nobody stays harmless. These sets add the missing shapes:
    two groups on one set, a direct grant layered on a group grant, two named
    users on top of a group that already contains one of them, a set naming a
    group with no members, and one set governing two documents.
    """
    conn.execute(
        """
        INSERT INTO users (id, email, name) VALUES
          ('00000000-0000-0000-0000-000000000005', 'both@example.test', 'Both Groups'),
          ('00000000-0000-0000-0000-000000000006', 'inert@example.test', 'Inert Group'),
          ('00000000-0000-0000-0000-000000000007', 'redundant@example.test',
           'Redundant Grant'),
          ('00000000-0000-0000-0000-000000000008', 'reused@example.test', 'Reused Set');
        INSERT INTO groups (id, name) VALUES
          ('10000000-0000-0000-0000-000000000002', 'security'),
          ('10000000-0000-0000-0000-000000000003', 'inert'),
          ('10000000-0000-0000-0000-000000000004', 'vacant');
        -- 'inert' has a member and grants nothing; 'vacant' grants a set and has
        -- no member. Both must leave access unchanged, for opposite reasons.
        INSERT INTO group_memberships (group_id, user_id) VALUES
          ('10000000-0000-0000-0000-000000000001',
           '00000000-0000-0000-0000-000000000005'),
          ('10000000-0000-0000-0000-000000000002',
           '00000000-0000-0000-0000-000000000005'),
          ('10000000-0000-0000-0000-000000000003',
           '00000000-0000-0000-0000-000000000006'),
          ('10000000-0000-0000-0000-000000000001',
           '00000000-0000-0000-0000-000000000007');

        INSERT INTO permission_sets (id, visibility) VALUES
          ('20000000-0000-0000-0000-000000000006', 'restricted'),
          ('20000000-0000-0000-0000-000000000007', 'restricted'),
          ('20000000-0000-0000-0000-000000000008', 'restricted'),
          ('20000000-0000-0000-0000-000000000009', 'restricted'),
          ('20000000-0000-0000-0000-000000000010', 'restricted');
        INSERT INTO permission_set_groups (permission_set_id, group_id) VALUES
          -- One set, two groups: reachable by either, which is a union not an AND.
          ('20000000-0000-0000-0000-000000000006',
           '10000000-0000-0000-0000-000000000001'),
          ('20000000-0000-0000-0000-000000000006',
           '10000000-0000-0000-0000-000000000002'),
          ('20000000-0000-0000-0000-000000000007',
           '10000000-0000-0000-0000-000000000002'),
          ('20000000-0000-0000-0000-000000000008',
           '10000000-0000-0000-0000-000000000001'),
          -- A set whose only group has no members reaches nobody.
          ('20000000-0000-0000-0000-000000000009',
           '10000000-0000-0000-0000-000000000004');
        INSERT INTO permission_set_users (permission_set_id, user_id) VALUES
          -- A direct grant layered on top of a group grant on the same set.
          ('20000000-0000-0000-0000-000000000007',
           '00000000-0000-0000-0000-000000000002'),
          -- Two named users on a set that also names a group; the second user is
          -- already in that group, so the direct grant must change nothing.
          ('20000000-0000-0000-0000-000000000008',
           '00000000-0000-0000-0000-000000000002'),
          ('20000000-0000-0000-0000-000000000008',
           '00000000-0000-0000-0000-000000000007'),
          ('20000000-0000-0000-0000-000000000010',
           '00000000-0000-0000-0000-000000000008');

        INSERT INTO documents (
          id, source, kind, external_id, root_document_id, permission_set_id,
          title, body, container, raw_payload
        ) VALUES
          ('30000000-0000-0000-0000-000000000011', 'confluence', 'page', 'SHARED-1',
           '30000000-0000-0000-0000-000000000011',
           '20000000-0000-0000-0000-000000000006',
           'Shared document', 'Shared body', 'Engineering', '{}'),
          ('30000000-0000-0000-0000-000000000012', 'confluence', 'page', 'SECURITY-1',
           '30000000-0000-0000-0000-000000000012',
           '20000000-0000-0000-0000-000000000007',
           'Security document', 'Security body', 'Security', '{}'),
          ('30000000-0000-0000-0000-000000000013', 'github', 'pull_request', 'PAIR-1',
           '30000000-0000-0000-0000-000000000013',
           '20000000-0000-0000-0000-000000000008',
           'Pair document', 'Pair body', 'northstar/browser', '{}'),
          ('30000000-0000-0000-0000-000000000014', 'slack', 'message', 'VACANT-1',
           '30000000-0000-0000-0000-000000000014',
           '20000000-0000-0000-0000-000000000009',
           'Vacant document', 'Vacant body', '#vacant', '{}'),
          -- One permission set governing two documents.
          ('30000000-0000-0000-0000-000000000015', 'confluence', 'page', 'REUSED-1',
           '30000000-0000-0000-0000-000000000015',
           '20000000-0000-0000-0000-000000000010',
           'Reused document one', 'Reused body one', 'Archive', '{}'),
          ('30000000-0000-0000-0000-000000000016', 'confluence', 'page', 'REUSED-2',
           '30000000-0000-0000-0000-000000000016',
           '20000000-0000-0000-0000-000000000010',
           'Reused document two', 'Reused body two', 'Archive', '{}');

        INSERT INTO chunks (
          source, id, document_id, field, text, chunk_index, content_hash, metadata
        )
        SELECT source, source || ':' || external_id || ':0', id, 'body', body, 0,
               external_id || '-hash', jsonb_build_object('external_id', external_id)
        FROM documents WHERE external_id IN (
          'SHARED-1', 'SECURITY-1', 'PAIR-1', 'VACANT-1', 'REUSED-1', 'REUSED-2'
        );

        -- Not a zero vector: cosine distance to one is undefined, so an HNSW
        -- scan never returns the row and every added shape would be untestable
        -- on the semantic path for the same reason a zero query vector hid the
        -- native result sets.
        INSERT INTO sentences (
          source, chunk_id, sentence_index, sentence, embedding, embedding_model
        )
        SELECT source, source || ':' || external_id || ':0', 0, body,
               ('[' || array_to_string(array_fill('0.1'::text, ARRAY[1536]), ',') || ']')::halfvec,
               'test-embedding'
        FROM documents WHERE external_id IN (
          'SHARED-1', 'SECURITY-1', 'PAIR-1', 'VACANT-1', 'REUSED-1', 'REUSED-2'
        );
        """
    )


def _seed_malformed_root_chain(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        INSERT INTO documents (
          id, source, kind, external_id, parent_document_id, root_document_id,
          permission_set_id, title, body
        ) VALUES
          ('30000000-0000-0000-0000-000000000009', 'jira', 'comment',
           'INTERMEDIATE-ROOT', '30000000-0000-0000-0000-000000000005',
           '30000000-0000-0000-0000-000000000005',
           '20000000-0000-0000-0000-000000000001', 'Intermediate', 'Intermediate body'),
          ('30000000-0000-0000-0000-000000000010', 'jira', 'comment',
           'CHAIN-CHILD', '30000000-0000-0000-0000-000000000009',
           '30000000-0000-0000-0000-000000000009',
           '20000000-0000-0000-0000-000000000001', 'Chain child', 'Chain child body');

        INSERT INTO chunks (
          source, id, document_id, field, text, chunk_index, content_hash
        ) VALUES (
          'jira', 'jira:CHAIN-CHILD:0',
          '30000000-0000-0000-0000-000000000010',
          'body', 'Chain child body', 0, 'chain-child-hash'
        );
        INSERT INTO sentences (
          source, chunk_id, sentence_index, sentence, embedding, embedding_model
        ) VALUES (
          'jira', 'jira:CHAIN-CHILD:0', 0, 'Chain child body',
          ('[' || array_to_string(array_fill('0'::text, ARRAY[1536]), ',') || ']')::halfvec,
          'test-embedding'
        );
        """
    )
