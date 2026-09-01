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

    schema = Path(__file__).parents[2] / "db" / "init" / "001_schema.sql"
    with psycopg.connect(url) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
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
