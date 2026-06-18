"""Tests for publish schedule computation."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from uploader.registry import UploadEntry
from uploader.scheduler import compute_publish_schedule, parse_start, to_rfc3339_utc


def test_to_rfc3339_utc() -> None:
    dt = datetime(2026, 6, 21, 9, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    result = to_rfc3339_utc(dt)
    assert result.endswith("Z")
    assert "T" in result
    # 9 AM EDT = 13:00 UTC
    assert result == "2026-06-21T13:00:00Z"


def test_compute_publish_schedule() -> None:
    pending = [
        UploadEntry(id="j1", channel_id="a"),
        UploadEntry(id="j2", channel_id="a"),
    ]
    start = datetime(2026, 6, 21, 9, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    plan = compute_publish_schedule(pending, start, interval_hours=24)

    assert len(plan) == 2
    assert plan[0][0].id == "j1"
    assert plan[0][1] == "2026-06-21T13:00:00Z"
    assert plan[1][1] == "2026-06-22T13:00:00Z"


def test_compute_publish_schedule_no_schedule() -> None:
    pending = [UploadEntry(id="j1", channel_id="a")]
    start = datetime(2026, 6, 21, 9, 0, 0, tzinfo=timezone.utc)
    plan = compute_publish_schedule(pending, start, 24, no_schedule=True)
    assert plan[0][1] == ""


def test_parse_start_explicit() -> None:
    dt = parse_start("2026-06-21 09:00", timezone_name="America/New_York")
    assert dt.hour == 9
    assert dt.minute == 0
    assert dt.tzinfo is not None


def test_parse_start_default_tomorrow() -> None:
    dt = parse_start(None, timezone_name="America/New_York", default_hour=9)
    assert dt.hour == 9
    assert dt.minute == 0
