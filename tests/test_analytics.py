"""Unit tests for analytics health classification and aggregation helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from uploader.analytics_service import (
    _category_health,
    _metric_block,
    _subs_per_1k_views,
    _views_per_upload,
    build_category_out,
    build_cohort_compare,
    channel_to_out,
)
from uploader.youtube_analytics import (
    ChannelPerformance,
    PeriodTotals,
    VideoVelocity,
    _build_velocity_from_days,
    _compute_median_views_24h,
    _mark_underperformers,
    classify_health,
    date_windows,
    _delta_pct,
)


def test_delta_pct():
    assert _delta_pct(110, 100) == 10.0
    assert _delta_pct(0, 0) is None
    assert _delta_pct(50, 0) == 100.0


def test_classify_health_growing_on_views():
    assert (
        classify_health(views=120, views_prior=100, subs_net=0, subs_net_prior=0, has_data=True)
        == "growing"
    )


def test_classify_health_cooling():
    assert (
        classify_health(views=80, views_prior=100, subs_net=0, subs_net_prior=0, has_data=True)
        == "cooling"
    )


def test_classify_health_flat():
    assert (
        classify_health(views=105, views_prior=100, subs_net=1, subs_net_prior=1, has_data=True)
        == "flat"
    )


def test_classify_health_needs_data():
    assert (
        classify_health(views=0, views_prior=0, subs_net=0, subs_net_prior=0, has_data=False)
        == "needs_data"
    )


def test_date_windows_28():
    from datetime import date

    start, end, prior_start, prior_end = date_windows(28, end=date(2026, 7, 14))
    assert end.isoformat() == "2026-07-14"
    assert start.isoformat() == "2026-06-17"
    assert prior_end.isoformat() == "2026-06-16"
    assert prior_start.isoformat() == "2026-05-20"


def test_metric_block():
    m = _metric_block(120, 100)
    assert m["value"] == 120
    assert m["prior"] == 100
    assert m["delta_pct"] == 20.0


def test_views_per_upload():
    assert _views_per_upload(1000, 4) == 250.0
    assert _views_per_upload(1000, 0) is None


def test_category_health_weighted():
    growing = ChannelPerformance(
        channel_id="a",
        name="A",
        category="lofi",
        ok=True,
        status_code="growing",
        current=PeriodTotals(views=900),
    )
    cooling = ChannelPerformance(
        channel_id="b",
        name="B",
        category="lofi",
        ok=True,
        status_code="cooling",
        current=PeriodTotals(views=100),
    )
    assert _category_health([growing, cooling]) == "growing"


def test_build_category_out_carrier_risk():
    a = ChannelPerformance(
        channel_id="a",
        name="Star",
        category="lofi",
        ok=True,
        status_code="growing",
        current=PeriodTotals(views=800, watch_minutes=100),
        prior=PeriodTotals(views=400),
        uploads_in_window=2,
        sparkline=[10, 20, 30],
    )
    b = ChannelPerformance(
        channel_id="b",
        name="Small",
        category="lofi",
        ok=True,
        status_code="flat",
        current=PeriodTotals(views=200, watch_minutes=20),
        prior=PeriodTotals(views=200),
        uploads_in_window=2,
        sparkline=[5, 5, 5],
    )
    out = build_category_out("lofi", [a, b], network_views=2000, include_channels=True)
    assert out["carrier_risk"] is True
    assert out["channel_count"] == 2
    assert out["views"]["value"] == 1000
    assert out["views_per_upload"] == 250.0
    assert len(out["channels"]) == 2
    assert any("Carrier risk" in i for i in out["insights"])


def test_channel_to_out_shape():
    perf = ChannelPerformance(
        channel_id="ch1",
        name="Test",
        category="korean",
        ok=True,
        source="analytics_api",
        status_code="growing",
        current=PeriodTotals(
            views=500,
            watch_minutes=40,
            subscribers_gained=12,
            subscribers_lost=2,
            ctr=4.5,
            avg_view_percentage=35.0,
        ),
        prior=PeriodTotals(views=400, watch_minutes=30, subscribers_gained=10, subscribers_lost=2),
        uploads_in_window=5,
        sparkline=[1, 2, 3],
    )
    out = channel_to_out(perf, days=28)
    assert out["channel_id"] == "ch1"
    assert out["subs_net"]["value"] == 10
    assert out["views_per_upload"] == 100.0
    assert out["ctr"]["value"] == 4.5
    assert out["subs_per_day"] == round(10 / 28, 4)
    assert out["subs_per_1k_views"] == round((10 / 500) * 1000, 4)
    assert out["studio_url"] == "https://studio.youtube.com/"
    assert out["channel_url"] == ""


def test_channel_to_out_urls():
    perf = ChannelPerformance(
        channel_id="ch1",
        name="Test",
        category="korean",
        youtube_channel_id="UCabc",
        custom_url="@coolchannel",
        thumbnail_url="https://example.com/a.jpg",
        ok=True,
        source="analytics_api",
        status_code="growing",
        current=PeriodTotals(views=100),
        prior=PeriodTotals(views=90),
    )
    out = channel_to_out(perf, days=28)
    assert out["channel_url"] == "https://www.youtube.com/@coolchannel"
    assert out["studio_url"] == "https://studio.youtube.com/channel/UCabc"
    assert out["thumbnail_url"] == "https://example.com/a.jpg"
    assert out["custom_url"] == "@coolchannel"


def test_build_velocity_from_days_checkpoints():
    pub = "2026-07-01T12:00:00Z"
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    day_rows = [
        ("2026-07-01", 100.0, 10.0),
        ("2026-07-02", 50.0, 5.0),
        ("2026-07-03", 30.0, 3.0),
        ("2026-07-04", 20.0, 2.0),
        ("2026-07-05", 10.0, 1.0),
        ("2026-07-06", 5.0, 0.5),
        ("2026-07-07", 2.0, 0.2),
    ]
    m = _build_velocity_from_days(
        published_at=pub,
        day_rows=day_rows,
        lifetime_views=500.0,
        now=now,
    )
    assert m["views_24h"] == 100.0
    assert m["views_72h"] == 180.0
    assert m["views_7d"] == 217.0
    assert m["watch_24h"] == 10.0
    assert m["watch_72h"] == 18.0


def test_build_velocity_from_days_too_young():
    pub = "2026-07-10T12:00:00Z"
    now = datetime(2026, 7, 11, 6, 0, tzinfo=timezone.utc)
    day_rows = [("2026-07-10", 50.0, 5.0)]
    m = _build_velocity_from_days(
        published_at=pub,
        day_rows=day_rows,
        lifetime_views=50.0,
        now=now,
    )
    assert m["views_24h"] is None
    assert m["views_72h"] is None


def test_median_views_24h_and_underperformer():
    videos = [
        VideoVelocity(video_id="a", age_hours=72, views_24h=200.0, views_72h=400.0),
        VideoVelocity(video_id="b", age_hours=96, views_24h=100.0, views_72h=80.0),
        VideoVelocity(video_id="c", age_hours=50, views_24h=300.0, views_72h=500.0),
    ]
    median = _compute_median_views_24h(videos)
    assert median == 200.0
    _mark_underperformers(videos, median)
    flagged = [v for v in videos if v.is_underperformer]
    assert len(flagged) == 1
    assert flagged[0].video_id == "b"


def test_subs_per_1k_views():
    assert _subs_per_1k_views(10, 5000) == 2.0
    assert _subs_per_1k_views(10, 0) is None


def test_build_cohort_compare():
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    videos = [
        {
            "published_at": "2026-07-14T10:00:00Z",
            "age_hours": 100,
            "views_72h": 200.0,
            "views_7d": 500.0,
            "views_so_far": 600.0,
        },
        {
            "published_at": "2026-07-13T10:00:00Z",
            "age_hours": 50,
            "views_72h": None,
            "views_7d": None,
            "views_so_far": 100.0,
        },
        {
            "published_at": "2026-07-05T10:00:00Z",
            "age_hours": 240,
            "views_72h": 100.0,
            "views_7d": 300.0,
            "views_so_far": 400.0,
        },
    ]
    cohorts = build_cohort_compare(videos, now=now)
    assert cohorts["this_week"]["uploads"] == 2
    assert cohorts["last_week"]["uploads"] == 1
    assert cohorts["this_week"]["avg_views_72h"] == 200.0
    assert cohorts["last_week"]["avg_views_72h"] == 100.0
    assert cohorts["delta_views_72h_pct"] == 100.0
