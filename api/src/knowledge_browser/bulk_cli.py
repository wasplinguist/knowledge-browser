"""Safe operator commands for the isolated Redwood import database."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys

import psycopg

from .bulk_import import run_import
from .bulk_state import assert_redwood_database, reset_redwood_database
from .bulk_verify import MAX_P95_MS, verify_redwood
from .config import database_url
from .dataset import validate_streaming_dataset
from .embedding_index import (
    MAX_EMBEDDING_CONCURRENCY,
    MAX_EMBEDDING_INPUTS,
    MAX_ESTIMATED_TOKENS,
    EmbeddingRequestConfig,
)
from .profiles import load_profile


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA = PROJECT_ROOT / "data" / "redwood"
SCHEMAS = (
    PROJECT_ROOT / "db" / "init" / "001_schema.sql",
    PROJECT_ROOT / "db" / "init" / "002_bulk_import.sql",
)
RELEASED_PROFILE = PROJECT_ROOT / "search" / "profiles" / "released.json"
SAFE_ERRORS = {
    "batch_import_failed",
    "embedding_provider_failed",
    "embedding_provider_invalid_response",
    "missing_api_key",
}
STALL_AFTER_SECONDS = 150.0
FAILURES = {
    "invalid_manifest": (
        "Redwood command failed: reason=invalid_manifest; "
        "next_step=fix the dataset, then run validate again.\n"
    ),
    "changed_state": (
        "Redwood command failed: reason=changed_state; "
        "next_step=use the original dataset and model, or check the target "
        "before an intentional reset.\n"
    ),
    "missing_api_key": (
        "Redwood command failed: reason=missing_api_key; "
        "next_step=set OPENAI_API_KEY, then run the command again.\n"
    ),
    "schema_failure": (
        "Redwood command failed: reason=schema_failure; "
        "next_step=check Redwood database access and schema, then retry; "
        "reset changes roll back.\n"
    ),
    "verify_incompatible": (
        "Redwood command failed: reason=verify_incompatible; "
        "next_step=review the safe report and run status before retrying.\n"
    ),
    "import_failed": (
        "Redwood command failed: reason=import_failed; "
        "next_step=run status, fix the reported issue, then rerun run; "
        "do not reset valid progress.\n"
    ),
    "status_failed": (
        "Redwood command failed: reason=status_failed; "
        "next_step=check Redwood database access, then retry status.\n"
    ),
    "verify_failed": (
        "Redwood command failed: reason=verify_failed; "
        "next_step=run status, fix the database or dataset setup, then retry verify.\n"
    ),
}
COMMAND_FAILURES = {
    "validate": "invalid_manifest",
    "reset": "schema_failure",
    "run": "import_failed",
    "status": "status_failed",
    "verify": "verify_failed",
}


class SafeCommandError(RuntimeError):
    def __init__(self, safe_code: str):
        super().__init__(safe_code)
        self.safe_code = safe_code


def _openai_client():
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise SafeCommandError("missing_api_key")
    from openai import OpenAI

    return OpenAI()


def _connection_factory(url):
    return lambda: psycopg.connect(url)


def _database_url(args):
    return args.database_url or database_url()


def _validated_dataset(path):
    try:
        return validate_streaming_dataset(path)
    except Exception as error:
        raise SafeCommandError("invalid_manifest") from error


def _print_failure(reason):
    sys.stderr.write(FAILURES[reason])


def _add_database_argument(parser):
    parser.add_argument("--database-url", help=argparse.SUPPRESS)


def _bounded_int(name, maximum=None):
    def parse(value):
        number = int(value)
        if number < 1 or (maximum is not None and number > maximum):
            limit = f" and at most {maximum}" if maximum is not None else ""
            raise argparse.ArgumentTypeError(
                f"{name} must be positive{limit}"
            )
        return number

    return parse


def _positive_float(name):
    def parse(value):
        number = float(value)
        if number <= 0 or not math.isfinite(number):
            raise argparse.ArgumentTypeError(f"{name} must be positive")
        return number

    return parse


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
    defaults = EmbeddingRequestConfig()
    run.add_argument(
        "--document-batch-size", type=_bounded_int("document batch size"), default=100
    )
    run.add_argument(
        "--embedding-batch-size", type=_bounded_int("embedding batch size"), default=100
    )
    run.add_argument(
        "--work-window-size", type=_bounded_int("work window size"), default=200
    )
    run.add_argument(
        "--embedding-concurrency",
        type=_bounded_int("embedding concurrency", MAX_EMBEDDING_CONCURRENCY),
        default=defaults.concurrency,
    )
    run.add_argument(
        "--embedding-max-inputs",
        type=_bounded_int("embedding max inputs", MAX_EMBEDDING_INPUTS),
        default=defaults.max_inputs,
    )
    run.add_argument(
        "--embedding-max-tokens",
        type=_bounded_int("embedding max tokens", MAX_ESTIMATED_TOKENS),
        default=defaults.max_estimated_tokens,
    )
    for flag, default in (
        ("connect", defaults.connect_timeout),
        ("read", defaults.read_timeout),
        ("write", defaults.write_timeout),
        ("total", defaults.total_timeout),
    ):
        run.add_argument(
            f"--embedding-{flag}-timeout",
            type=_positive_float(f"embedding {flag} timeout"),
            default=default,
        )
    _add_database_argument(run)

    status = commands.add_parser("status")
    _add_database_argument(status)

    verify = commands.add_parser("verify")
    verify.add_argument("--data", type=Path, default=DEFAULT_DATA)
    verify.add_argument("--json", action="store_true")
    _add_database_argument(verify)
    return parser


def _print_batch(report):
    print(
        f"source={report.source} next_line={report.next_line} "
        f"documents={report.documents} chunks={report.chunks} "
        f"sentences={report.sentences} cache_hits={report.cache_hits} "
        f"cache_misses={report.cache_misses} "
        f"provider_requests={report.provider_calls} "
        f"concurrency={report.concurrency} retries={report.retries} "
        f"sentences_per_second={report.sentences_per_second:.2f} "
        f"estimated_remaining_seconds={report.estimated_remaining_seconds:.2f} "
        f"elapsed_seconds={report.elapsed_seconds:.2f}",
        flush=True,
    )


def _print_run(result):
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
            SELECT id, status, safe_error, dataset_version, manifest_digest,
                   embedding_model, embedding_dimensions,
                   EXTRACT(EPOCH FROM (pg_catalog.now() - started_at)),
                   EXTRACT(EPOCH FROM (pg_catalog.now() - updated_at))
            FROM public.bulk_import_runs
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
        if run is None:
            print("No Redwood import run.")
            return
        (
            run_id,
            status,
            safe_error,
            dataset_version,
            manifest_digest,
            embedding_model,
            dimensions,
            elapsed,
            updated_age,
        ) = run
        if status == "loading":
            status = (
                "stalled"
                if float(updated_age) > STALL_AFTER_SECONDS
                else "running"
            )
        safe_error = safe_error if safe_error in SAFE_ERRORS else None
        suffix = f" safe_error={safe_error}" if safe_error else ""
        print(
            f"run={run_id} status={status} dataset_version={dataset_version} "
            f"manifest_digest={manifest_digest} embedding_model={embedding_model} "
            f"dimensions={dimensions} "
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


def _print_verification(report):
    print(
        f"compatible={'yes' if report.compatible else 'no'} "
        f"documents={report.counts['documents']} "
        f"chunks={report.counts['chunks']} "
        f"sentences={report.counts['sentences']} "
        f"missing_embeddings={report.missing_embeddings}"
    )
    print("sources=" + json.dumps(report.sources, sort_keys=True))
    print("acl_checks=" + json.dumps(report.acl_checks, sort_keys=True))
    print(
        f"recall_at_10={report.recall_at_10:.4f} mrr={report.mrr:.4f} "
        f"p50_ms={report.p50_ms:.2f} p95_ms={report.p95_ms:.2f}"
    )


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.command == "reset" and not args.yes:
        sys.stderr.write("Reset refused; pass --yes after checking the target.\n")
        return 1

    try:
        if args.command == "validate":
            dataset = _validated_dataset(args.data)
            print(
                "Redwood dataset is valid: "
                f"artifacts={dataset.manifest['counts']['artifacts']}"
            )
        elif args.command == "reset":
            _validated_dataset(args.data)
            url = _database_url(args)
            reset_redwood_database(url, SCHEMAS)
            print("Redwood database reset.")
        elif args.command == "run":
            dataset = _validated_dataset(args.data)
            url = _database_url(args)
            assert_redwood_database(url)
            request_config = EmbeddingRequestConfig(
                concurrency=args.embedding_concurrency,
                max_inputs=args.embedding_max_inputs,
                max_estimated_tokens=args.embedding_max_tokens,
                connect_timeout=args.embedding_connect_timeout,
                read_timeout=args.embedding_read_timeout,
                write_timeout=args.embedding_write_timeout,
                total_timeout=args.embedding_total_timeout,
            )
            result = run_import(
                _connection_factory(url),
                dataset,
                _openai_client,
                document_batch_size=args.document_batch_size,
                embedding_batch_size=args.embedding_batch_size,
                work_window_size=args.work_window_size,
                request_config=request_config,
                progress_callback=_print_batch,
            )
            _print_run(result)
        elif args.command == "status":
            _print_status(_database_url(args))
        else:
            dataset = _validated_dataset(args.data)
            url = _database_url(args)
            assert_redwood_database(url)
            report = verify_redwood(
                _connection_factory(url),
                dataset.root,
                _openai_client(),
                load_profile(RELEASED_PROFILE),
            )
            if args.json:
                print(json.dumps(report.safe_dict(), sort_keys=True))
            else:
                _print_verification(report)
            if not report.compatible or report.p95_ms > MAX_P95_MS:
                _print_failure("verify_incompatible")
                return 1
    except Exception as error:
        reason = getattr(error, "safe_code", COMMAND_FAILURES[args.command])
        if reason not in FAILURES:
            reason = COMMAND_FAILURES[args.command]
        _print_failure(reason)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
