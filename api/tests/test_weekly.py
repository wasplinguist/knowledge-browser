from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb

from knowledge_browser.weekly import build_weekly_report, write_weekly_report


pytestmark = pytest.mark.integration


def test_weekly_report_is_read_only_and_finds_reformulations(db, tmp_path):
    start = datetime(2026, 8, 30, 9, tzinfo=timezone.utc)
    user = UUID("00000000-0000-0000-0000-000000000001")
    session = UUID("90000000-0000-0000-0000-000000000001")
    first = db.execute(
        """
        INSERT INTO search_events (
          created_at, user_id, session_id, query, normalized_query, profile,
          result_ids, result_count, embedding_available, duration_ms
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """,
        (start, user, session, "nrel", "nrel", "released", Jsonb([]), 0, False, 100),
    ).fetchone()[0]
    second = db.execute(
        """
        INSERT INTO search_events (
          created_at, user_id, session_id, query, normalized_query, profile,
          result_ids, result_count, embedding_available, duration_ms
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """,
        (
            start + timedelta(minutes=2), user, session, "Nimbus Relay",
            "nimbus relay", "released",
            Jsonb([{"source": "jira", "external_id": "COMPANY-1"}]),
            1, False, 200,
        ),
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO search_clicks (
          created_at, search_id, user_id, source, external_id, rank
        ) VALUES (%s,%s,%s,%s,%s,%s)
        """,
        (start + timedelta(minutes=3), second, user, "jira", "COMPANY-1", 1),
    )
    before = db.execute("SELECT count(*) FROM search_events").fetchone()[0]

    report = build_weekly_report(db, start, start + timedelta(days=1))
    paths = write_weekly_report(report, tmp_path)

    assert first != second
    assert report.total_searches == 2
    assert report.no_result_rate == 0.5
    assert report.click_through_rate == 0.5
    assert report.reformulations[0].queries == ["nrel", "nimbus relay"]
    assert db.execute("SELECT count(*) FROM search_events").fetchone()[0] == before
    assert all(path.is_file() for path in paths)


def test_weekly_report_excludes_named_synthetic_profiles(db):
    start = datetime(2026, 8, 30, 9, tzinfo=timezone.utc)
    user = UUID("00000000-0000-0000-0000-000000000001")
    for profile, query in (("released", "real"), ("demo-loop-v1", "fake")):
        db.execute(
            """
            INSERT INTO search_events (
              created_at, user_id, query, normalized_query, profile, result_ids,
              result_count, embedding_available, duration_ms
            ) VALUES (%s,%s,%s,%s,%s,%s,0,false,100)
            """,
            (start, user, query, query, profile, Jsonb([])),
        )

    report = build_weekly_report(
        db, start, start + timedelta(days=1), ("demo-loop-v1",)
    )

    assert report.total_searches == 1
    assert report.top_queries[0].query == "real"
    assert report.excluded_profiles == ("demo-loop-v1",)
