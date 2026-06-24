"""Tests for publish schedule computation."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from uploader.channels import AppConfig, ChannelConfig, GoogleConfig, PublishConfig
from uploader.registry import UploadEntry
from uploader.channel_list import infer_schedule_interval_hours
from uploader.scheduler import (
    build_channel_upload_plan,
    compute_publish_schedule,
    effective_interval_hours,
    parse_start,
    resolve_publish_start,
    to_rfc3339_utc,
)


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


def test_resolve_publish_start_after_youtube_tail() -> None:
    channel = ChannelConfig(
        id="ch1",
        name="Test",
        token_path="secrets/ch1/token.json",
        registry_path="state/ch1/registry.txt",
        publish=PublishConfig(timezone="America/New_York", hour=9, interval_hours=24),
    )
    tail = datetime(2026, 6, 25, 13, 0, 0, tzinfo=timezone.utc)
    start_dt = resolve_publish_start(
        channel,
        scheduled_publish_ats=[tail],
    )
    plan = compute_publish_schedule(
        [UploadEntry(id="j1", channel_id="ch1"), UploadEntry(id="j2", channel_id="ch1")],
        start_dt,
        24,
    )
    assert plan[0][1] == "2026-06-26T13:00:00Z"
    assert plan[1][1] == "2026-06-27T13:00:00Z"


def test_resolve_publish_start_no_scheduled_falls_back() -> None:
    channel = ChannelConfig(
        id="ch1",
        name="Test",
        token_path="secrets/ch1/token.json",
        registry_path="state/ch1/registry.txt",
        publish=PublishConfig(timezone="UTC", hour=9, interval_hours=24),
    )
    explicit = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)
    start_dt = resolve_publish_start(
        channel,
        start="2026-07-01 09:00",
        scheduled_publish_ats=[datetime(2026, 6, 25, 13, 0, 0, tzinfo=timezone.utc)],
    )
    assert start_dt == explicit


def _test_channel() -> ChannelConfig:
    return ChannelConfig(
        id="ch1",
        name="Test",
        token_path="secrets/ch1/token.json",
        registry_path="state/ch1/registry.txt",
        publish=PublishConfig(timezone="America/New_York", hour=9, interval_hours=24),
    )


def _test_config() -> AppConfig:
    return AppConfig(channels=[_test_channel()], google=GoogleConfig())


def test_build_channel_upload_plan_immediate_when_no_scheduled(monkeypatch) -> None:
    channel = _test_channel()
    pending = [UploadEntry(id="j1", channel_id="ch1"), UploadEntry(id="j2", channel_id="ch1")]

    def _empty(*args, **kwargs):
        return []

    monkeypatch.setattr(
        "uploader.channel_list.fetch_scheduled_publish_datetimes",
        _empty,
    )
    plan = build_channel_upload_plan(channel, _test_config(), pending)
    assert plan.upload_immediately is True
    assert plan.anchor == "immediate"
    assert plan.items[0][1] == ""
    assert plan.items[1][1] == ""


def test_build_channel_upload_plan_after_youtube_tail(monkeypatch) -> None:
    channel = _test_channel()
    pending = [UploadEntry(id="j1", channel_id="ch1"), UploadEntry(id="j2", channel_id="ch1")]
    tail = datetime(2026, 6, 25, 13, 0, 0, tzinfo=timezone.utc)

    def _scheduled(*args, **kwargs):
        return [tail]

    monkeypatch.setattr(
        "uploader.channel_list.fetch_scheduled_publish_datetimes",
        _scheduled,
    )
    plan = build_channel_upload_plan(channel, _test_config(), pending)
    assert plan.upload_immediately is False
    assert plan.anchor == "youtube_tail"
    assert plan.items[0][1] == "2026-06-26T13:00:00Z"
    assert plan.items[1][1] == "2026-06-27T13:00:00Z"


def test_infer_schedule_interval_hours_median() -> None:
    from datetime import timedelta

    base = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
    scheduled = [base + timedelta(hours=24 * i) for i in range(4)]
    assert infer_schedule_interval_hours(scheduled) == 24.0


def test_infer_schedule_interval_hours_single() -> None:
    assert infer_schedule_interval_hours([datetime(2026, 1, 1, tzinfo=timezone.utc)]) is None


def test_effective_interval_explicit_over_inferred() -> None:
    channel = _test_channel()
    scheduled = [
        datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 2, 9, 0, 0, tzinfo=timezone.utc),
    ]
    assert effective_interval_hours(channel, scheduled, 12.0) == 12.0


def test_effective_interval_inferred_from_youtube() -> None:
    channel = _test_channel()
    scheduled = [
        datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 2, 9, 0, 0, tzinfo=timezone.utc),
    ]
    assert effective_interval_hours(channel, scheduled, None) == 24.0


def test_build_channel_upload_plan_infers_interval(monkeypatch) -> None:
    channel = _test_channel()
    pending = [UploadEntry(id="j1", channel_id="ch1")]
    t1 = datetime(2026, 6, 20, 13, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 21, 13, 0, 0, tzinfo=timezone.utc)

    def _scheduled(*args, **kwargs):
        return [t1, t2]

    monkeypatch.setattr(
        "uploader.channel_list.fetch_scheduled_publish_datetimes",
        _scheduled,
    )
    plan = build_channel_upload_plan(channel, _test_config(), pending)
    assert plan.interval_hours == 24.0
    assert plan.items[0][1] == "2026-06-22T13:00:00Z"
