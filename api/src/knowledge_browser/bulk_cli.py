"""Safe operator commands for the isolated Redwood import database."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

import psycopg

from .bulk_import import run_import
from .bulk_state import assert_redwood_database, reset_redwood_database
from .config import database_url
from .dataset import SOURCES, validate_streaming_dataset


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA = PROJECT_ROOT / "data" / "redwood"
SCHEMAS = (
    PROJECT_ROOT / "db" / "init" / "001_schema.sql",
    PROJECT_ROOT / "db" / "init" / "002_bulk_import.sql",
)
SAFE_ERRORS = {
    "batch_import_failed",
    "embedding_provider_failed",
    "embedding_provider_invalid_response",
}
ERRORS = {
    "validate": "Redwood dataset validation failed.\n",
    "reset": "Redwood reset failed; no database changes were completed.\n",
    "run": "Redwood import failed; run status for safe details.\n",
    "status": "Redwood status failed; check database access.\n",
    "verify": "Redwood verification failed; check status first.\n",
}


def _openai_client():
    from openai import OpenAI

    return OpenAI()


def _connection_factory(url):
    return lambda: psycopg.connect(url)


def _database_url(args):
    return args.database_url or database_url()


def _add_database_argument(parser):
    parser.add_argument("--database-url", help=argparse.SUPPRESS)


def _parser():
    parser = argparse.ArgumentParser(description="Manage the Redwood import database.")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("--data", type=Path, default=DEFAULT_DATA)

    reset = commands.add_parser("reset")
    reset.add_argument("--data", type=Path, default=DEFAULT_DATA)
    reset.add_argument("--yes", action="store_true")
    _add_database_argument(reset)

    run = commands.add_parser("run")
    run.add_argument("--data", type=Path, default=DEFAULT_DATA)
    run.add_argument("--document-batch-size", type=int, default=100)
    run.add_argument("--embedding-batch-size", type=int, default=100)
    _add_database_argument(run)

    status = commands.add_parser("status")
    _add_database_argument(status)

    verify = commands.add_parser("verify")
    verify.add_argument("--data", type=Path, default=DEFAULT_DATA)
    verify.add_argument("--json", action="store_true")
    _add_database_argument(verify)
    return parser


def _batch_sources(dataset, reports):
    source_index = 0
    sources = list(SOURCES)
    for report in reports:
        while source_index < len(sources):
            source = sources[source_index]
            size = (dataset.root / "artifacts" / f"{source}.jsonl").stat().st_size
            if size:
                break
            source_index += 1
        if source_index == len(sources):
            raise RuntimeError("bulk report does not match the dataset")
        yield source, report
        if report.next_offset >= size:
            source_index += 1


def _print_run(result, dataset, started):
    for source, report in _batch_sources(dataset, result.batches):
        elapsed = time.monotonic() - started
        print(
            f"source={source} next_line={report.next_line} "
            f"documents={report.documents} sentences={report.sentences} "
            f"elapsed_seconds={elapsed:.2f} provider_calls={report.provider_calls}"
        )
    print(
        f"run={result.run_id} "
        f"load_complete={'yes' if result.complete else 'no'} "
        f"provider_calls={result.provider_calls}"
    )


def _print_status(url):
    with psycopg.connect(url) as conn:
        exists = conn.execute(
            "SELECT pg_catalog.to_regclass('public.bulk_import_runs')"
        ).fetchone()[0]
        if exists is None:
            print("No Redwood import run.")
            return
        run = conn.execute(
            """
            SELECT id, status, safe_error,
                   EXTRACT(EPOCH FROM (pg_catalog.now() - started_at))
            FROM public.bulk_import_runs
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
        if run is None:
            print("No Redwood import run.")
            return
        run_id, status, safe_error, elapsed = run
        safe_error = safe_error if safe_error in SAFE_ERRORS else None
        suffix = f" safe_error={safe_error}" if safe_error else ""
        print(
            f"run={run_id} status={status} "
            f"elapsed_seconds={float(elapsed):.2f}{suffix}"
        )
        progress = conn.execute(
            """
            SELECT source, next_line, documents, chunks, sentences
            FROM public.bulk_import_progress
            WHERE run_id = %s
            ORDER BY source
            """,
            (run_id,),
        )
        for source, next_line, documents, chunks, sentences in progress:
            print(
                f"source={source} next_line={next_line} documents={documents} "
                f"chunks={chunks} sentences={sentences}"
            )


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.command == "reset" and not args.yes:
        sys.stderr.write("Reset refused; pass --yes after checking the target.\n")
        return 1

    try:
        if args.command == "validate":
            dataset = validate_streaming_dataset(args.data)
            print(
                "Redwood dataset is valid: "
                f"artifacts={dataset.manifest['counts']['artifacts']}"
            )
        elif args.command == "reset":
            validate_streaming_dataset(args.data)
            reset_redwood_database(_database_url(args), SCHEMAS)
            print("Redwood database reset.")
        elif args.command == "run":
            dataset = validate_streaming_dataset(args.data)
            url = _database_url(args)
            assert_redwood_database(url)
            started = time.monotonic()
            result = run_import(
                _connection_factory(url),
                dataset,
                _openai_client,
                document_batch_size=args.document_batch_size,
                embedding_batch_size=args.embedding_batch_size,
            )
            _print_run(result, dataset, started)
        elif args.command == "status":
            _print_status(_database_url(args))
        else:
            validate_streaming_dataset(args.data)
            assert_redwood_database(_database_url(args))
            raise RuntimeError("verification is not implemented")
    except Exception:
        sys.stderr.write(ERRORS[args.command])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
