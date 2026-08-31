from dataclasses import asdict
from uuid import UUID

import pytest

from knowledge_browser.repository import (
    get_chunk_sentences,
    get_document,
    get_document_chunks,
    resolve_identity,
)


pytestmark = pytest.mark.integration

COMPANY_USER = UUID("00000000-0000-0000-0000-000000000001")
DIRECT_USER = UUID("00000000-0000-0000-0000-000000000002")
GROUP_USER = UUID("00000000-0000-0000-0000-000000000003")
OTHER_USER = UUID("00000000-0000-0000-0000-000000000004")
UNKNOWN_USER = UUID("00000000-0000-0000-0000-000000000099")


def test_resolve_identity_accepts_email_or_canonical_uuid(db):
    by_email = resolve_identity(db, "group@example.test")
    by_id = resolve_identity(db, str(GROUP_USER))

    assert by_email == by_id
    assert by_email.id == GROUP_USER
    assert by_email.email == "group@example.test"
    assert resolve_identity(db, "missing@example.test") is None


@pytest.mark.parametrize(
    ("user_id", "source", "external_id"),
    [
        (COMPANY_USER, "jira", "COMPANY-1"),
        (DIRECT_USER, "confluence", "DIRECT-1"),
        (GROUP_USER, "slack", "GROUP-1"),
    ],
)
def test_document_reads_allow_company_direct_and_group_users(
    db, user_id, source, external_id
):
    document = get_document(db, user_id, source, external_id)

    assert document is not None
    assert document.id is not None
    assert document.source == source
    assert document.external_id == external_id


def test_document_preserves_canonical_content_and_provenance_without_acl_metadata(db):
    document = get_document(db, COMPANY_USER, "jira", "COMPANY-1")

    assert asdict(document) == {
        "id": UUID("30000000-0000-0000-0000-000000000001"),
        "source": "jira",
        "kind": "issue",
        "external_id": "COMPANY-1",
        "parent_document_id": None,
        "root_document_id": UUID("30000000-0000-0000-0000-000000000001"),
        "title": "Company document",
        "body": "Company body",
        "author": "Ada",
        "url": "https://example.test/company",
        "container": "Atlas",
        "raw_payload": {"provenance": {"tenant": "northstar"}},
        "source_created_at": document.source_created_at,
        "source_updated_at": document.source_updated_at,
        "indexed_at": document.indexed_at,
    }
    assert document.source_created_at.isoformat() == "2026-08-01T01:00:00+00:00"
    assert "permission" not in repr(document).lower()


@pytest.mark.parametrize(
    ("user_id", "source", "external_id"),
    [
        (OTHER_USER, "confluence", "DIRECT-1"),
        (OTHER_USER, "slack", "GROUP-1"),
        (COMPANY_USER, "github", "MISSING-1"),
        (UNKNOWN_USER, "jira", "COMPANY-1"),
        (COMPANY_USER, "jira", "DOES-NOT-EXIST"),
        (COMPANY_USER, "jira", "VISIBLE-CHILD"),
        (COMPANY_USER, "jira", "HIDDEN-CHILD"),
    ],
)
def test_forbidden_missing_and_root_child_documents_are_indistinguishable(
    db, user_id, source, external_id
):
    assert get_document(db, user_id, source, external_id) is None
    assert get_document_chunks(db, user_id, source, external_id) == []


def test_chunks_and_sentences_are_typed_ordered_and_acl_filtered(db):
    chunks = get_document_chunks(db, COMPANY_USER, "jira", "COMPANY-1")
    sentences = get_chunk_sentences(db, COMPANY_USER, "jira", chunks[0].id)

    assert [(chunk.id, chunk.chunk_index) for chunk in chunks] == [
        ("jira:COMPANY-1:0", 0)
    ]
    assert chunks[0].document_id == UUID("30000000-0000-0000-0000-000000000001")
    assert chunks[0].metadata == {"external_id": "COMPANY-1"}
    assert [(sentence.chunk_id, sentence.sentence_index) for sentence in sentences] == [
        ("jira:COMPANY-1:0", 0)
    ]
    assert sentences[0].embedding_model == "test-embedding"
    assert sentences[0].embedding.startswith("[")

    assert get_chunk_sentences(db, UNKNOWN_USER, "jira", chunks[0].id) == []
    assert get_chunk_sentences(db, COMPANY_USER, "slack", chunks[0].id) == []


@pytest.mark.parametrize(
    ("external_id", "chunk_id"),
    [
        ("VISIBLE-CHILD", "jira:VISIBLE-CHILD:0"),
        ("HIDDEN-CHILD", "jira:HIDDEN-CHILD:0"),
    ],
)
def test_root_child_acl_has_zero_chunk_or_sentence_leaks(db, external_id, chunk_id):
    assert get_document_chunks(db, COMPANY_USER, "jira", external_id) == []
    assert get_chunk_sentences(db, COMPANY_USER, "jira", chunk_id) == []
