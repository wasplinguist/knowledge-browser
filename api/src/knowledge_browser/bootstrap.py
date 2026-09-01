"""Initialize an empty Knowledge Browser database."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import sys

from .dataset import load_dataset
from .db import connection
from .db_compat import check_compatibility
from .embedding_index import collect_sentences, create_embeddings
from .importer import ImportReport, import_dataset
from .profiles import load_profile


ROOT = Path(__file__).parents[3]
DATA_DIR = ROOT / "data" / "company"
PROFILE_PATH = ROOT / "search" / "profiles" / "released.json"
PARTIAL_COUNT_SQL = """
    SELECT
      (SELECT count(*) FROM users)
      + (SELECT count(*) FROM groups)
      + (SELECT count(*) FROM group_memberships)
      + (SELECT count(*) FROM permission_sets)
      + (SELECT count(*) FROM permission_set_users)
      + (SELECT count(*) FROM permission_set_groups)
      + (SELECT count(*) FROM chunks)
      + (SELECT count(*) FROM sentences)
"""


class BootstrapError(Exception):
    """A safe, actionable bootstrap state error."""


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    imported: bool
    report: ImportReport | None


def bootstrap_database(connection_factory, data_dir, client_factory) -> BootstrapResult:
    """Import a verified dataset once, without altering existing product data."""
    with connection_factory() as conn:
        if conn.execute("SELECT count(*) FROM documents").fetchone()[0]:
            if not check_compatibility(conn).compatible:
                raise BootstrapError("existing database is incompatible")
            return BootstrapResult(False, None)
        if conn.execute(PARTIAL_COUNT_SQL).fetchone()[0]:
            raise BootstrapError("database is partially initialized")

        dataset = load_dataset(data_dir)
        model = load_profile(PROFILE_PATH).embedding_model
        vectors = create_embeddings(
            client_factory(), collect_sentences(dataset.documents), model
        )
        with conn.transaction():
            report = import_dataset(conn, dataset, vectors, model=model)
            if not check_compatibility(conn).compatible:
                raise BootstrapError("imported database failed compatibility check")
        return BootstrapResult(True, report)


def _openai_client():
    if not os.environ.get("OPENAI_API_KEY"):
        raise BootstrapError("OPENAI_API_KEY is required for first-time setup")
    from openai import OpenAI

    return OpenAI()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize an empty database")
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)

    try:
        result = bootstrap_database(connection, args.data, _openai_client)
    except BootstrapError as error:
        print(f"bootstrap failed: {error}", file=sys.stderr)
        return 1
    except Exception:
        print("bootstrap failed", file=sys.stderr)
        return 1

    if not result.imported:
        print("database already initialized")
        return 0

    report = result.report
    assert report is not None
    print(
        "database initialized: "
        f"users={report.users} documents={report.documents} "
        f"chunks={report.chunks} sentences={report.sentences}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
