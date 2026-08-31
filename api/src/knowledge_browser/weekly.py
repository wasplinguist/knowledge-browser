"""Read-only local search behavior reports."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from statistics import median
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class QuerySummary:
    query: str
    searches: int


@dataclass(frozen=True)
class Reformulation:
    session_id: UUID
    queries: list[str]


@dataclass(frozen=True)
class WeeklyReport:
    since: datetime
    until: datetime
    total_searches: int
    unique_queries: int
    no_result_rate: float
    click_through_rate: float
    p50_duration_ms: float
    p95_duration_ms: float
    top_queries: list[QuerySummary]
    top_no_result_queries: list[QuerySummary]
    top_unclicked_queries: list[QuerySummary]
    reformulations: list[Reformulation]
    excluded_profiles: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["since"] = self.since.astimezone(timezone.utc).isoformat()
        value["until"] = self.until.astimezone(timezone.utc).isoformat()
        value["reformulations"] = [
            {**item, "session_id": str(item["session_id"])}
            for item in value["reformulations"]
        ]
        return value


def _summaries(events, predicate=lambda _event: True) -> list[QuerySummary]:
    counts: dict[str, int] = {}
    for event in events:
        if predicate(event):
            query = event["query"]
            counts[query] = counts.get(query, 0) + 1
    return [
        QuerySummary(query, count)
        for query, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
    ]


def _reformulations(events) -> list[Reformulation]:
    sessions: dict[UUID, list[dict[str, Any]]] = {}
    for event in events:
        if event["session_id"]:
            sessions.setdefault(event["session_id"], []).append(event)
    result = []
    for session_id, items in sessions.items():
        group: list[dict[str, Any]] = []
        for event in sorted(items, key=lambda item: item["created_at"]):
            if group and event["created_at"] - group[-1]["created_at"] > timedelta(minutes=10):
                queries = list(dict.fromkeys(item["query"] for item in group))
                if len(queries) > 1:
                    result.append(Reformulation(session_id, queries))
                group = []
            group.append(event)
        queries = list(dict.fromkeys(item["query"] for item in group))
        if len(queries) > 1:
            result.append(Reformulation(session_id, queries))
    return result


def build_weekly_report(
    conn,
    since: datetime,
    until: datetime,
    excluded_profiles: tuple[str, ...] = (),
) -> WeeklyReport:
    rows = conn.execute(
        """
        SELECT event.created_at, event.session_id, event.normalized_query,
               event.result_count, event.duration_ms, MIN(click.created_at)
        FROM search_events event
        LEFT JOIN search_clicks click ON click.search_id = event.id
        WHERE event.created_at >= %s AND event.created_at < %s
          AND NOT (event.profile = ANY(%s))
        GROUP BY event.id
        ORDER BY event.created_at, event.id
        """,
        (since, until, list(excluded_profiles)),
    ).fetchall()
    events = [
        {
            "created_at": row[0], "session_id": row[1], "query": row[2],
            "result_count": row[3], "duration_ms": row[4], "clicked_at": row[5],
        }
        for row in rows
    ]
    total = len(events)
    durations = sorted(item["duration_ms"] for item in events)
    return WeeklyReport(
        since=since,
        until=until,
        total_searches=total,
        unique_queries=len({item["query"] for item in events}),
        no_result_rate=(sum(item["result_count"] == 0 for item in events) / total if total else 0.0),
        click_through_rate=(sum(item["clicked_at"] is not None for item in events) / total if total else 0.0),
        p50_duration_ms=float(median(durations)) if durations else 0.0,
        p95_duration_ms=(float(durations[math.ceil(0.95 * len(durations)) - 1]) if durations else 0.0),
        top_queries=_summaries(events),
        top_no_result_queries=_summaries(events, lambda item: item["result_count"] == 0),
        top_unclicked_queries=_summaries(events, lambda item: item["clicked_at"] is None),
        reformulations=_reformulations(events),
        excluded_profiles=excluded_profiles,
    )


def _lines(items: list[QuerySummary]) -> list[str]:
    return [f"- `{item.query}` — {item.searches}" for item in items] or ["- None"]


def weekly_markdown(report: WeeklyReport) -> str:
    reformulations = [
        "- " + " → ".join(f"`{query}`" for query in item.queries)
        for item in report.reformulations
    ] or ["- None"]
    return "\n".join([
        "# Weekly search behavior", "",
        f"Period: {report.since.isoformat()} to {report.until.isoformat()}",
        "Excluded profiles: " + (", ".join(report.excluded_profiles) or "None"), "",
        f"Searches: {report.total_searches}",
        f"No-result rate: {report.no_result_rate:.1%}",
        f"Click-through rate: {report.click_through_rate:.1%}",
        f"p50 / p95 latency: {report.p50_duration_ms:.0f} / {report.p95_duration_ms:.0f} ms", "",
        "## Top queries", "", *_lines(report.top_queries), "",
        "## No-result queries", "", *_lines(report.top_no_result_queries), "",
        "## Unclicked queries", "", *_lines(report.top_unclicked_queries), "",
        "## Reformulations", "", *reformulations, "",
        "This report is local behavior evidence. It does not choose or promote a search profile.", "",
    ])


def write_weekly_report(report: WeeklyReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = output_dir / report.until.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ-weekly")
    json_path = stem.with_suffix(".json")
    markdown_path = stem.with_suffix(".md")
    json_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(weekly_markdown(report))
    return json_path, markdown_path
