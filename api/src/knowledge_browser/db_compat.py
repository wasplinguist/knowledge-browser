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
REQUIRED_COLUMNS: dict[str, dict[str, tuple[str, bool, str]]] = {
    "users": {
        "id": ("uuid", True, ""),
        "email": ("text", True, ""),
        "name": ("text", True, ""),
        "raw_payload": ("jsonb", True, ""),
    },
    "groups": {
        "id": ("uuid", True, ""),
        "name": ("text", True, ""),
        "raw_payload": ("jsonb", True, ""),
    },
    "group_memberships": {
        "group_id": ("uuid", True, ""),
        "user_id": ("uuid", True, ""),
    },
    "permission_sets": {
        "id": ("uuid", True, ""),
        "visibility": ("text", True, ""),
        "raw_payload": ("jsonb", True, ""),
    },
    "permission_set_users": {
        "permission_set_id": ("uuid", True, ""),
        "user_id": ("uuid", True, ""),
    },
    "permission_set_groups": {
        "permission_set_id": ("uuid", True, ""),
        "group_id": ("uuid", True, ""),
    },
    "documents": {
        "id": ("uuid", True, ""),
        "source": ("text", True, ""),
        "kind": ("text", True, ""),
        "external_id": ("text", True, ""),
        "parent_document_id": ("uuid", False, ""),
        "root_document_id": ("uuid", True, ""),
        "permission_set_id": ("uuid", True, ""),
        "title": ("text", True, ""),
        "body": ("text", True, ""),
        "author": ("text", False, ""),
        "url": ("text", False, ""),
        "container": ("text", False, ""),
        "raw_payload": ("jsonb", True, ""),
        "source_created_at": ("timestamp with time zone", False, ""),
        "source_updated_at": ("timestamp with time zone", False, ""),
        "indexed_at": ("timestamp with time zone", True, ""),
    },
    "chunks": {
        "source": ("text", True, ""),
        "id": ("text", True, ""),
        "document_id": ("uuid", True, ""),
        "field": ("text", True, ""),
        "text": ("text", True, ""),
        "chunk_index": ("integer", True, ""),
        "content_hash": ("text", True, ""),
        "metadata": ("jsonb", True, ""),
        "fts": ("tsvector", False, "s"),
    },
    "sentences": {
        "source": ("text", True, ""),
        "id": ("bigint", True, ""),
        "chunk_id": ("text", True, ""),
        "sentence_index": ("integer", True, ""),
        "sentence": ("text", True, ""),
        "embedding": ("halfvec(1536)", False, ""),
        "embedding_model": ("text", True, ""),
    },
}
REQUIRED_IDENTITIES = {("sentences", "id"): "a"}
REQUIRED_GENERATED_EXPRESSIONS = {
    ("chunks", "fts"): "to_tsvector('english'::regconfig, text)"
}
REQUIRED_INDEXES = {
    "chunks_fts_idx": ("chunks", "gin", ("fts",), ("tsvector_ops",)),
    "sentences_embedding_idx": (
        "sentences",
        "hnsw",
        ("embedding",),
        ("halfvec_cosine_ops",),
    ),
}
REQUIRED_CONSTRAINTS = {
    "users_pkey": ("users", "p", ("id",), None, ()),
    "users_email_key": ("users", "u", ("email",), None, ()),
    "groups_pkey": ("groups", "p", ("id",), None, ()),
    "groups_name_key": ("groups", "u", ("name",), None, ()),
    "group_memberships_pkey": (
        "group_memberships", "p", ("group_id", "user_id"), None, ()
    ),
    "group_memberships_group_id_fkey": (
        "group_memberships", "f", ("group_id",), "groups", ("id",)
    ),
    "group_memberships_user_id_fkey": (
        "group_memberships", "f", ("user_id",), "users", ("id",)
    ),
    "permission_sets_pkey": ("permission_sets", "p", ("id",), None, ()),
    "permission_set_users_pkey": (
        "permission_set_users", "p", ("permission_set_id", "user_id"), None, ()
    ),
    "permission_set_users_permission_set_id_fkey": (
        "permission_set_users", "f", ("permission_set_id",),
        "permission_sets", ("id",)
    ),
    "permission_set_users_user_id_fkey": (
        "permission_set_users", "f", ("user_id",), "users", ("id",)
    ),
    "permission_set_groups_pkey": (
        "permission_set_groups", "p", ("permission_set_id", "group_id"), None, ()
    ),
    "permission_set_groups_permission_set_id_fkey": (
        "permission_set_groups", "f", ("permission_set_id",),
        "permission_sets", ("id",)
    ),
    "permission_set_groups_group_id_fkey": (
        "permission_set_groups", "f", ("group_id",), "groups", ("id",)
    ),
    "documents_pkey": ("documents", "p", ("id",), None, ()),
    "documents_source_external_id_key": (
        "documents", "u", ("source", "external_id"), None, ()
    ),
    "documents_parent_document_id_fkey": (
        "documents", "f", ("parent_document_id",), "documents", ("id",)
    ),
    "documents_root_document_id_fkey": (
        "documents", "f", ("root_document_id",), "documents", ("id",)
    ),
    "documents_permission_set_id_fkey": (
        "documents", "f", ("permission_set_id",), "permission_sets", ("id",)
    ),
    "chunks_pkey": ("chunks", "p", ("source", "id"), None, ()),
    "chunks_document_id_fkey": (
        "chunks", "f", ("document_id",), "documents", ("id",)
    ),
    "sentences_pkey": ("sentences", "p", ("source", "id"), None, ()),
    "sentences_source_chunk_id_fkey": (
        "sentences", "f", ("source", "chunk_id"),
        "chunks", ("source", "id")
    ),
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

    columns = {
        (table, column): (data_type, not_null, generated, identity, expression)
        for table, column, data_type, not_null, generated, identity, expression in conn.execute(
            """
            SELECT class.relname, attribute.attname,
                   format_type(attribute.atttypid, attribute.atttypmod),
                   attribute.attnotnull, attribute.attgenerated,
                   attribute.attidentity,
                   pg_get_expr(default_value.adbin, default_value.adrelid)
            FROM pg_attribute attribute
            JOIN pg_class class ON class.oid = attribute.attrelid
            JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
            LEFT JOIN pg_attrdef default_value
              ON default_value.adrelid = attribute.attrelid
             AND default_value.adnum = attribute.attnum
            WHERE namespace.nspname = 'public'
              AND class.relname = ANY(%s)
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
            """,
            (list(REQUIRED_COLUMNS),),
        )
    }
    for table, required_columns in REQUIRED_COLUMNS.items():
        for column, expected in required_columns.items():
            actual = columns.get((table, column))
            if actual is None:
                issues.append(f"missing column: {table}.{column}")
                continue
            if actual[0] != expected[0]:
                issues.append(f"invalid column type: {table}.{column}")
            if actual[1] != expected[1]:
                issues.append(f"invalid column nullability: {table}.{column}")
            if actual[2] != expected[2]:
                issues.append(f"invalid generated column: {table}.{column}")
            if actual[3] != REQUIRED_IDENTITIES.get((table, column), ""):
                issues.append(f"invalid identity column: {table}.{column}")
            expected_expression = REQUIRED_GENERATED_EXPRESSIONS.get((table, column))
            if expected_expression is not None and actual[4] != expected_expression:
                issues.append(f"invalid generated expression: {table}.{column}")

    indexes: dict[str, list[tuple[object, ...]]] = {}
    for name, table, method, valid, ready, keys, opclasses in conn.execute(
            """
            SELECT index_class.relname, table_class.relname, access_method.amname,
                   index.indisvalid, index.indisready,
                   ARRAY(
                     SELECT pg_get_indexdef(index.indexrelid, position, true)
                     FROM generate_series(1, index.indnkeyatts) position
                     ORDER BY position
                   ),
                   ARRAY(
                     SELECT operator_class.opcname
                     FROM unnest(index.indclass::oid[]) WITH ORDINALITY item(oid, position)
                     JOIN pg_opclass operator_class ON operator_class.oid = item.oid
                     WHERE item.position <= index.indnkeyatts
                     ORDER BY item.position
                   )
            FROM pg_index index
            JOIN pg_class index_class ON index_class.oid = index.indexrelid
            JOIN pg_class table_class ON table_class.oid = index.indrelid
            JOIN pg_am access_method ON access_method.oid = index_class.relam
            JOIN pg_namespace namespace ON namespace.oid = table_class.relnamespace
            WHERE namespace.nspname = 'public' AND index_class.relname = ANY(%s)
            """,
            (list(REQUIRED_INDEXES),),
        ):
        indexes.setdefault(name, []).append(
            (table, method, tuple(keys), tuple(opclasses), valid, ready)
        )
    for name, expected in REQUIRED_INDEXES.items():
        if name not in indexes:
            issues.append(f"missing index: {name}")
        elif not any(actual[:4] == expected and actual[4] and actual[5] for actual in indexes[name]):
            issues.append(f"invalid index: {name}")

    partitions: dict[str, list[tuple[str, str]]] = {}
    for child, parent, bound in conn.execute(
            """
            SELECT child.relname, parent.relname,
                   pg_get_expr(child.relpartbound, child.oid)
            FROM pg_inherits inheritance
            JOIN pg_class child ON child.oid = inheritance.inhrelid
            JOIN pg_class parent ON parent.oid = inheritance.inhparent
            JOIN pg_namespace namespace ON namespace.oid = child.relnamespace
            WHERE namespace.nspname = 'public'
            """
        ):
        partitions.setdefault(child, []).append((parent, bound))
    expected_partitions = {
        **{f"{source}_chunks": ("chunks", f"FOR VALUES IN ('{source}')") for source in SOURCES},
        **{f"{source}_sentences": ("sentences", f"FOR VALUES IN ('{source}')") for source in SOURCES},
    }
    for child, expected in expected_partitions.items():
        if child not in partitions:
            issues.append(f"missing partition: {child} -> {expected[0]}")
        elif expected not in partitions[child]:
            issues.append(f"invalid partition: {child}")

    constraints: dict[str, list[tuple[object, ...]]] = {}
    for name, table, kind, valid, columns_, referenced_table, referenced_columns in conn.execute(
            """
            SELECT constraint_record.conname, table_class.relname,
                   constraint_record.contype, constraint_record.convalidated,
                   ARRAY(
                     SELECT attribute.attname
                     FROM unnest(constraint_record.conkey)
                       WITH ORDINALITY key(attnum, position)
                     JOIN pg_attribute attribute
                       ON attribute.attrelid = constraint_record.conrelid
                      AND attribute.attnum = key.attnum
                     ORDER BY key.position
                   ),
                   referenced_class.relname,
                   ARRAY(
                     SELECT attribute.attname
                     FROM unnest(constraint_record.confkey)
                       WITH ORDINALITY key(attnum, position)
                     JOIN pg_attribute attribute
                       ON attribute.attrelid = constraint_record.confrelid
                      AND attribute.attnum = key.attnum
                     ORDER BY key.position
                   )
            FROM pg_constraint constraint_record
            JOIN pg_class table_class
              ON table_class.oid = constraint_record.conrelid
            LEFT JOIN pg_class referenced_class
              ON referenced_class.oid = constraint_record.confrelid
            JOIN pg_namespace namespace ON namespace.oid = table_class.relnamespace
            WHERE namespace.nspname = 'public'
              AND constraint_record.conname = ANY(%s)
            """,
            (list(REQUIRED_CONSTRAINTS),),
        ):
        constraints.setdefault(name, []).append(
            (
                table,
                kind,
                tuple(columns_),
                referenced_table,
                tuple(referenced_columns),
                valid,
            )
        )
    for name, expected in REQUIRED_CONSTRAINTS.items():
        if name not in constraints:
            issues.append(f"missing constraint: {name}")
        elif not any(actual[:5] == expected and actual[5] for actual in constraints[name]):
            issues.append(f"invalid constraint: {name}")

    document_count = chunk_count = sentence_count = embedded_sentence_count = 0
    document_source_counts = {source: 0 for source in SOURCES}
    if {"documents", "chunks", "sentences"} <= tables and all(
        (table, column) in columns
        for table in ("documents", "chunks", "sentences")
        for column in REQUIRED_COLUMNS[table]
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

        non_canonical_roots = conn.execute(
            """
            SELECT count(*)
            FROM documents document
            JOIN documents root ON root.id = document.root_document_id
            WHERE root.root_document_id <> root.id
            """
        ).fetchone()[0]
        if non_canonical_roots:
            issues.append("documents contain non-canonical roots")

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

        if "permission_sets" in tables:
            missing_permission_sets = conn.execute(
                """
                SELECT count(*)
                FROM documents
                LEFT JOIN permission_sets
                  ON permission_sets.id = documents.permission_set_id
                WHERE permission_sets.id IS NULL
                """
            ).fetchone()[0]
            if missing_permission_sets:
                issues.append("documents contain missing permission sets")

    acl_relationships = (
        (
            "permission_set_users",
            "permission_sets",
            "users",
            "permission_set_id",
            "user_id",
        ),
        (
            "permission_set_groups",
            "permission_sets",
            "groups",
            "permission_set_id",
            "group_id",
        ),
        (
            "group_memberships",
            "groups",
            "users",
            "group_id",
            "user_id",
        ),
    )
    for link, left_table, right_table, left_key, right_key in acl_relationships:
        if {link, left_table, right_table} <= tables:
            broken_links = conn.execute(
                f"""
                SELECT count(*)
                FROM {link} link
                LEFT JOIN {left_table} left_record ON left_record.id = link.{left_key}
                LEFT JOIN {right_table} right_record ON right_record.id = link.{right_key}
                WHERE left_record.id IS NULL OR right_record.id IS NULL
                """
            ).fetchone()[0]
            if broken_links:
                issues.append(f"{link} contain broken relationships")

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
