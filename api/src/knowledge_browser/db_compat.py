from dataclasses import dataclass
import json
import sys

from .db import connection


SOURCES = ("confluence", "github", "jira", "slack")
REQUIRED_EXTENSIONS = {"pgcrypto", "vector"}
REQUIRED_TABLES = {
    "users",
    "groups",
    "group_memberships",
    "permission_sets",
    "permission_set_users",
    "permission_set_groups",
    "documents",
    "chunks",
    "sentences",
    *(f"{source}_chunks" for source in SOURCES),
    *(f"{source}_sentences" for source in SOURCES),
}
REQUIRED_COLUMNS = {
    "users": {"id", "email", "name", "raw_payload"},
    "groups": {"id", "name", "raw_payload"},
    "group_memberships": {"group_id", "user_id"},
    "permission_sets": {"id", "visibility", "raw_payload"},
    "permission_set_users": {"permission_set_id", "user_id"},
    "permission_set_groups": {"permission_set_id", "group_id"},
    "documents": {
        "id",
        "source",
        "kind",
        "external_id",
        "parent_document_id",
        "root_document_id",
        "permission_set_id",
        "title",
        "body",
        "author",
        "url",
        "container",
        "raw_payload",
        "source_created_at",
        "source_updated_at",
        "indexed_at",
    },
    "chunks": {
        "source",
        "id",
        "document_id",
        "field",
        "text",
        "chunk_index",
        "content_hash",
        "metadata",
        "fts",
    },
    "sentences": {
        "source",
        "id",
        "chunk_id",
        "sentence_index",
        "sentence",
        "embedding",
        "embedding_model",
    },
}
REQUIRED_INDEXES = {
    "chunks_fts_idx": "using gin (fts)",
    "sentences_embedding_idx": "using hnsw (embedding halfvec_cosine_ops)",
}
REQUIRED_CONSTRAINTS = {
    "users_pkey",
    "users_email_key",
    "groups_pkey",
    "groups_name_key",
    "group_memberships_pkey",
    "group_memberships_group_id_fkey",
    "group_memberships_user_id_fkey",
    "permission_sets_pkey",
    "permission_set_users_pkey",
    "permission_set_users_permission_set_id_fkey",
    "permission_set_users_user_id_fkey",
    "permission_set_groups_pkey",
    "permission_set_groups_permission_set_id_fkey",
    "permission_set_groups_group_id_fkey",
    "documents_pkey",
    "documents_source_external_id_key",
    "documents_parent_document_id_fkey",
    "documents_root_document_id_fkey",
    "documents_permission_set_id_fkey",
    "chunks_pkey",
    "chunks_document_id_fkey",
    "sentences_pkey",
    "sentences_source_chunk_id_fkey",
}


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    compatible: bool
    issues: tuple[str, ...]
    document_count: int
    chunk_count: int
    sentence_count: int
    embedded_sentence_count: int
    document_source_counts: dict[str, int]

    def safe_dict(self) -> dict[str, object]:
        return {
            "compatible": self.compatible,
            "issues": list(self.issues),
            "counts": {
                "documents": self.document_count,
                "chunks": self.chunk_count,
                "sentences": self.sentence_count,
                "embedded_sentences": self.embedded_sentence_count,
                "documents_by_source": self.document_source_counts,
            },
        }


def check_compatibility(conn) -> CompatibilityReport:
    issues: list[str] = []

    extensions = {
        row[0]
        for row in conn.execute(
            "SELECT extname FROM pg_extension WHERE extname = ANY(%s)",
            (list(REQUIRED_EXTENSIONS),),
        )
    }
    issues.extend(
        f"missing extension: {name}" for name in sorted(REQUIRED_EXTENSIONS - extensions)
    )

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    }
    issues.extend(f"missing table: {name}" for name in sorted(REQUIRED_TABLES - tables))

    columns = {}
    for table, column in conn.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ANY(%s)
        """,
        (list(REQUIRED_COLUMNS),),
    ):
        columns.setdefault(table, set()).add(column)
    for table, required in REQUIRED_COLUMNS.items():
        issues.extend(
            f"missing column: {table}.{column}"
            for column in sorted(required - columns.get(table, set()))
        )

    specialized_columns = {
        (table, column): (data_type, generated)
        for table, column, data_type, generated in conn.execute(
            """
            SELECT class.relname, attribute.attname,
                   format_type(attribute.atttypid, attribute.atttypmod),
                   attribute.attgenerated
            FROM pg_attribute attribute
            JOIN pg_class class ON class.oid = attribute.attrelid
            JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
            WHERE namespace.nspname = 'public'
              AND class.relname IN ('chunks', 'sentences')
              AND attribute.attname IN ('fts', 'embedding')
              AND NOT attribute.attisdropped
            """
        )
    }
    embedding = specialized_columns.get(("sentences", "embedding"))
    if embedding and embedding[0] != "halfvec(1536)":
        issues.append("invalid column type: sentences.embedding")
    fts = specialized_columns.get(("chunks", "fts"))
    if fts and fts[1] != "s":
        issues.append("invalid generated column: chunks.fts")

    indexes = {
        name: definition.lower()
        for name, definition in conn.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = ANY(%s)
            """,
            (list(REQUIRED_INDEXES),),
        )
    }
    for name, method in REQUIRED_INDEXES.items():
        if name not in indexes:
            issues.append(f"missing index: {name}")
        elif method not in indexes[name]:
            issues.append(f"invalid index: {name}")

    partitions = {
        (child, parent)
        for child, parent in conn.execute(
            """
            SELECT child, parent
            FROM (
              SELECT c.relname AS child, p.relname AS parent
              FROM pg_inherits i
              JOIN pg_class c ON c.oid = i.inhrelid
              JOIN pg_class p ON p.oid = i.inhparent
              JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE n.nspname = 'public'
            ) attached
            """
        )
    }
    expected_partitions = {
        (f"{source}_chunks", "chunks") for source in SOURCES
    } | {(f"{source}_sentences", "sentences") for source in SOURCES}
    issues.extend(
        f"missing partition: {child} -> {parent}"
        for child, parent in sorted(expected_partitions - partitions)
    )

    constraints = {
        name: valid
        for name, valid in conn.execute(
            """
            SELECT conname, convalidated
            FROM pg_constraint
            WHERE connamespace = 'public'::regnamespace
              AND conname = ANY(%s)
            """,
            (list(REQUIRED_CONSTRAINTS),),
        )
    }
    issues.extend(
        f"missing constraint: {name}"
        for name in sorted(REQUIRED_CONSTRAINTS - constraints.keys())
    )
    issues.extend(
        f"invalid constraint: {name}"
        for name in sorted(name for name, valid in constraints.items() if not valid)
    )

    document_count = chunk_count = sentence_count = embedded_sentence_count = 0
    document_source_counts = {source: 0 for source in SOURCES}
    if {"documents", "chunks", "sentences"} <= tables and all(
        not required - columns.get(table, set())
        for table, required in REQUIRED_COLUMNS.items()
        if table in {"documents", "chunks", "sentences"}
    ):
        document_count = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
        chunk_count = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        sentence_count, embedded_sentence_count = conn.execute(
            "SELECT count(*), count(embedding) FROM sentences"
        ).fetchone()
        document_source_counts.update(
            conn.execute(
                "SELECT source, count(*) FROM documents GROUP BY source"
            ).fetchall()
        )

        if document_count == 0:
            issues.append("documents are empty")
        if chunk_count == 0:
            issues.append("chunks are empty")
        if sentence_count == 0:
            issues.append("sentences are empty")
        if sentence_count != embedded_sentence_count:
            issues.append("sentences contain missing embeddings")

        for table in ("documents", "chunks", "sentences"):
            present_sources = {
                row[0] for row in conn.execute(f"SELECT DISTINCT source FROM {table}")
            }
            issues.extend(
                f"missing source in {table}: {source}"
                for source in sorted(set(SOURCES) - present_sources)
            )

        broken_roots = conn.execute(
            """
            SELECT count(*)
            FROM documents document
            LEFT JOIN documents root ON root.id = document.root_document_id
            WHERE root.id IS NULL
            """
        ).fetchone()[0]
        if broken_roots:
            issues.append("documents contain broken roots")

        orphaned_chunks = conn.execute(
            """
            SELECT count(*)
            FROM chunks
            LEFT JOIN documents ON documents.id = chunks.document_id
            WHERE documents.id IS NULL
            """
        ).fetchone()[0]
        if orphaned_chunks:
            issues.append("chunks contain orphaned documents")

    return CompatibilityReport(
        compatible=not issues,
        issues=tuple(issues),
        document_count=document_count,
        chunk_count=chunk_count,
        sentence_count=sentence_count,
        embedded_sentence_count=embedded_sentence_count,
        document_source_counts=document_source_counts,
    )


def main() -> int:
    try:
        with connection() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            report = check_compatibility(conn)
    except Exception:
        print(
            "compatibility check failed: database connection unavailable",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(report.safe_dict(), sort_keys=True))
    return 0 if report.compatible else 1


if __name__ == "__main__":
    raise SystemExit(main())
