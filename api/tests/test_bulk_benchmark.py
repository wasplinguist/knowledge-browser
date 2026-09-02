import json

import pytest

import knowledge_browser.bulk_benchmark as bulk_benchmark


pytestmark = pytest.mark.unit


def test_fixed_scheduler_comparison_is_fast_equivalent_and_database_free():
    result = bulk_benchmark.compare_schedulers(
        [f"sentence {index}." for index in range(1200)],
        provider_delay=0.05,
    )

    assert result["same_vectors"] is True
    assert result["legacy_provider_requests"] == 12
    assert result["new_provider_requests"] == 3
    assert result["throughput_ratio"] >= 5.0
    assert result["database_used"] is False


@pytest.mark.parametrize(
    ("platform", "rss", "traced", "expected"),
    [
        ("darwin", 5_000, 4_000, 5_000),
        ("linux", 5_000, 4_000, 5_120_000),
        ("linux", 1, 8_000, 8_000),
    ],
)
def test_peak_memory_normalizes_platform_rss(platform, rss, traced, expected):
    assert bulk_benchmark.peak_memory_bytes(
        traced_peak=traced,
        rss_peak=rss,
        platform=platform,
    ) == expected


@pytest.mark.parametrize(
    ("ratio", "memory", "exit_code"),
    [
        (5.0, 2_147_483_647, 0),
        (4.99, 2_147_483_647, 1),
        (5.0, 2_147_483_648, 1),
    ],
)
def test_cli_fails_closed_on_speed_or_memory_gate(
    monkeypatch, capsys, ratio, memory, exit_code
):
    monkeypatch.setattr(
        bulk_benchmark,
        "run_benchmark",
        lambda **_kwargs: {
            "throughput_ratio": ratio,
            "peak_memory_bytes": memory,
            "same_vectors": True,
        },
    )

    assert bulk_benchmark.main(["--data", "/safe/redwood"]) == exit_code
    assert json.loads(capsys.readouterr().out)["throughput_ratio"] == ratio
