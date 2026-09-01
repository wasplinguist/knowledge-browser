"""Safe aggregate verification for a completed Redwood import."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import statistics
import time
from typing import Any
from uuid import uuid4

from .dataset import SOURCES
from .db_compat import check_compatibility
from .embedding_index import create_embeddings
from .profiles import SearchProfile
from .search import hybrid_search


MAX_P95_MS = 2_000.0


@dataclass(frozen=True, slots=True)
class VerificationReport:
    compatible: bool
    counts: dict[str, int]
    sources: dict[str, int]
    missing_embeddings: int
    acl_checks: dict[str, bool | int | str | None]
    recall_at_10: float
    mrr: float
    p50_ms: float
    p95_ms: float

    def safe_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _read_qa(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid qa.jsonl line {line_number}") from error
            if not isinstance(row, dict):
                raise ValueError(f"invalid qa.jsonl line {line_number}")
            if (
                not isinstance(row.get("question"), str)
                or not row["question"]
                or not isinstance(row.get("expected_doc_ids"), list)
                or not row["expected_doc_ids"]
                or not isinstance(row.get("required_group_ids"), list)
            ):
                raise ValueError(f"invalid qa.jsonl line {line_number}")
            rows.append(row)
    if not rows:
        raise ValueError("qa.jsonl must not be empty")
    return rows


def _source_summary(root: Path) -> tuple[dict[str, int], bool]:
    counts = {}
    has_direct_acl = False
    for source in SOURCES:
        with (root / "artifacts" / f"{source}.jsonl").open("rb") as stream:
            count = 0
            for line in stream:
                count += 1
                acl = json.loads(line).get("acl", {})
                has_direct_acl = has_direct_acl or bool(acl.get("user_ids"))
            counts[source] = count
    return counts, has_direct_acl


def _counts(conn, model: str) -> dict[str, int]:
    users, groups, documents, chunks = conn.execute(
        """
        SELECT
          (SELECT count(*) FROM users),
          (SELECT count(*) FROM groups),
          (SELECT count(*) FROM documents),
          (SELECT count(*) FROM chunks)
        """
    ).fetchone()
    sentences, embedded, wrong_model = conn.execute(
        """
        SELECT count(*), count(embedding),
               count(*) FILTER (WHERE embedding_model <> %s)
        FROM sentences
        """,
        (model,),
    ).fetchone()
    return {
        "users": users,
        "groups": groups,
        "documents": documents,
        "chunks": chunks,
        "sentences": sentences,
        "embedded_sentences": embedded,
        "wrong_embedding_model": wrong_model,
    }


def _qa_user(conn, required_group_ids: list[str]):
    if not required_group_ids:
        row = conn.execute("SELECT id FROM users ORDER BY email LIMIT 1").fetchone()
        return row[0] if row else None
    row = conn.execute(
        """
        SELECT memberships.user_id
        FROM group_memberships memberships
        JOIN groups ON groups.id = memberships.group_id
        WHERE COALESCE(
          groups.raw_payload->>'acl_group_id', groups.raw_payload->>'id'
        ) = ANY(%s)
        GROUP BY memberships.user_id
        HAVING count(DISTINCT COALESCE(
          groups.raw_payload->>'acl_group_id', groups.raw_payload->>'id'
        )) = %s
        ORDER BY memberships.user_id
        LIMIT 1
        """,
        (required_group_ids, len(set(required_group_ids))),
    ).fetchone()
    return row[0] if row else None


def _representative(conn, kind: str):
    if kind == "company":
        return conn.execute(
            """
            SELECT documents.source, documents.external_id, users.id,
                   documents.permission_set_id, chunks.text
            FROM documents
            JOIN permission_sets
              ON permission_sets.id = documents.permission_set_id
            JOIN chunks ON chunks.document_id = documents.id
            CROSS JOIN LATERAL (
              SELECT id FROM users ORDER BY id LIMIT 1
            ) users
            WHERE documents.root_document_id = documents.id
              AND permission_sets.visibility = 'company'
            ORDER BY documents.source, documents.external_id,
                     chunks.chunk_index, chunks.id
            LIMIT 1
            """
        ).fetchone()
    if kind == "group":
        return conn.execute(
            """
            SELECT documents.source, documents.external_id, memberships.user_id,
                   documents.permission_set_id, chunks.text
            FROM documents
            JOIN chunks ON chunks.document_id = documents.id
            JOIN permission_set_groups access
              ON access.permission_set_id = documents.permission_set_id
            JOIN group_memberships memberships
              ON memberships.group_id = access.group_id
            WHERE documents.root_document_id = documents.id
            ORDER BY documents.source, documents.external_id, memberships.user_id,
                     chunks.chunk_index, chunks.id
            LIMIT 1
            """
        ).fetchone()
    return conn.execute(
        """
        SELECT documents.source, documents.external_id, access.user_id,
               documents.permission_set_id, chunks.text
        FROM documents
        JOIN chunks ON chunks.document_id = documents.id
        JOIN permission_set_users access
          ON access.permission_set_id = documents.permission_set_id
        WHERE documents.root_document_id = documents.id
        ORDER BY documents.source, documents.external_id, access.user_id,
                 chunks.chunk_index, chunks.id
        LIMIT 1
        """
    ).fetchone()


def _unauthorized_user(conn, permission_set_id):
    row = conn.execute(
        """
        SELECT users.id
        FROM users
        WHERE NOT EXISTS (
          SELECT 1 FROM permission_set_users direct_access
          WHERE direct_access.permission_set_id = %s
            AND direct_access.user_id = users.id
        )
          AND NOT EXISTS (
            SELECT 1
            FROM permission_set_groups group_access
            JOIN group_memberships memberships
              ON memberships.group_id = group_access.group_id
            WHERE group_access.permission_set_id = %s
              AND memberships.user_id = users.id
          )
        ORDER BY users.id
        LIMIT 1
        """,
        (permission_set_id, permission_set_id),
    ).fetchone()
    return row[0] if row else None


def _acl_checks(
    conn,
    representatives,
    embeddings,
    profile: SearchProfile,
    direct_applicable: bool,
):
    company = representatives["company"]
    group = representatives["group"]
    direct = representatives["direct"]

    def search(row, user_id):
        if not row:
            return []
        return hybrid_search(
            conn,
            user_id,
            row[4],
            embeddings[row[4]],
            profile=profile,
        )

    def protected_results(row, user_id):
        if not row:
            return 1
        return sum(
            item["source"] == row[0] and item["external_id"] == row[1]
            for item in search(row, user_id)
        )

    def visible(row):
        return bool(row and protected_results(row, row[2]))

    def unauthorized_results(row):
        if not row:
            return 1
        user = _unauthorized_user(conn, row[3])
        if user is None:
            return 1
        return protected_results(row, user)

    unknown_user = uuid4()
    unknown_rows = (
        (company, group, direct) if direct_applicable else (company, group)
    )

    return {
        "company_visible": visible(company),
        "group_visible": visible(group),
        "group_unauthorized_results": unauthorized_results(group),
        "direct_user_status": (
            "checked" if direct_applicable else "not_applicable"
        ),
        "direct_user_visible": visible(direct) if direct_applicable else None,
        "direct_unauthorized_results": (
            unauthorized_results(direct) if direct_applicable else None
        ),
        "unknown_user_results": sum(
            len(search(row, unknown_user))
            for row in unknown_rows
            if row
        ),
    }


def verify_redwood(
    connection_factory,
    data_dir: Path,
    embedding_client,
    profile: SearchProfile,
) -> VerificationReport:
    """Verify aggregate data, ACL safety, and released retrieval quality."""
    root = Path(data_dir)
    manifest = _read_json(root / "manifest.json")
    questions = _read_qa(root / "qa.jsonl")
    expected_sources, has_direct_acl = _source_summary(root)
    manifest_digest = hashlib.sha256(
        (root / "manifest.json").read_bytes()
    ).hexdigest()

    with connection_factory() as conn:
        compatibility = check_compatibility(conn)
        counts = _counts(conn, profile.embedding_model)
        run = conn.execute(
            """
            SELECT id, status, manifest_digest, dataset_version,
                   embedding_model, embedding_dimensions
            FROM bulk_import_runs
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
        progress_rows = (
            conn.execute(
                """
                SELECT source, next_line, documents, chunks, sentences
                FROM bulk_import_progress
                WHERE run_id = %s
                """,
                (run[0],),
            ).fetchall()
            if run
            else []
        )
        users = [
            (_qa_user(conn, question["required_group_ids"]), question)
            for question in questions
        ]
        accessible = [(user, question) for user, question in users if user]
        if not accessible:
            raise ValueError("qa.jsonl has no accessible questions")
        representatives = {
            "company": _representative(conn, "company"),
            "group": _representative(conn, "group"),
            "direct": (
                _representative(conn, "direct") if has_direct_acl else None
            ),
        }
        embedding_texts = [
            question["question"] for _, question in accessible
        ] + [
            row[4] for row in representatives.values() if row
        ]
        embeddings = create_embeddings(
            embedding_client,
            embedding_texts,
            profile.embedding_model,
        )

        found = 0
        reciprocal_ranks = []
        latencies = []
        for user, question in accessible:
            text = question["question"]
            started = time.perf_counter()
            results = hybrid_search(
                conn, user, text, embeddings[text], profile=profile
            )
            latencies.append((time.perf_counter() - started) * 1000)
            expected = set(question["expected_doc_ids"])
            ranked = [item["external_id"] for item in results[:10]]
            rank = next(
                (
                    index
                    for index, value in enumerate(ranked, start=1)
                    if value in expected
                ),
                None,
            )
            found += rank is not None
            reciprocal_ranks.append(1 / rank if rank else 0.0)

        acl_checks = _acl_checks(
            conn,
            representatives,
            embeddings,
            profile,
            has_direct_acl,
        )

    sources = compatibility.document_source_counts
    missing_embeddings = counts["sentences"] - counts["embedded_sentences"]
    expected_counts = manifest.get("counts", {})
    progress = {
        source: (next_line, documents, chunks, sentences)
        for source, next_line, documents, chunks, sentences in progress_rows
    }
    progress_matches = bool(
        set(progress) == set(SOURCES)
        and all(
            progress[source][0] == expected_sources[source] + 1
            and progress[source][1] == expected_sources[source]
            for source in SOURCES
        )
        and sum(row[1] for row in progress.values()) == counts["documents"]
        and sum(row[2] for row in progress.values()) == counts["chunks"]
        and sum(row[3] for row in progress.values()) == counts["sentences"]
    )
    run_matches = bool(
        run
        and run[1] == "complete"
        and run[2] == manifest_digest
        and run[3] == manifest.get("dataset_version")
        and run[4] == profile.embedding_model
        and run[5] == 1536
    )
    direct_safe = acl_checks["direct_user_status"] == "not_applicable" or (
        acl_checks["direct_user_status"] == "checked"
        and acl_checks["direct_user_visible"]
        and acl_checks["direct_unauthorized_results"] == 0
    )
    acl_safe = (
        acl_checks["company_visible"]
        and acl_checks["group_visible"]
        and acl_checks["group_unauthorized_results"] == 0
        and acl_checks["unknown_user_results"] == 0
        and direct_safe
    )
    p50_ms = statistics.median(latencies)
    p95_ms = (
        latencies[0]
        if len(latencies) == 1
        else statistics.quantiles(latencies, n=100, method="inclusive")[94]
    )
    compatible = bool(
        compatibility.compatible
        and run_matches
        and progress_matches
        and counts["users"] == expected_counts.get("employees")
        and counts["documents"] == expected_counts.get("artifacts")
        and len(questions) == expected_counts.get("qa")
        and len(accessible) == len(questions)
        and sources == expected_sources
        and missing_embeddings == 0
        and counts["wrong_embedding_model"] == 0
        and acl_safe
        and p95_ms <= MAX_P95_MS
    )
    return VerificationReport(
        compatible=compatible,
        counts=counts,
        sources=sources,
        missing_embeddings=missing_embeddings,
        acl_checks=acl_checks,
        recall_at_10=found / len(accessible),
        mrr=sum(reciprocal_ranks) / len(accessible),
        p50_ms=p50_ms,
        p95_ms=p95_ms,
    )
