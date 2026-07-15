"""Unit tests for analytics health classification and aggregation helpers."""

from __future__ import annotations

from uploader.analytics_service import (
    _category_health,
    _metric_block,
    _views_per_upload,
    build_category_out,
    channel_to_out,
)
from uploader.youtube_analytics import (
    ChannelPerformance,
    PeriodTotals,
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
    out = channel_to_out(perf)
    assert out["channel_id"] == "ch1"
    assert out["subs_net"]["value"] == 10
    assert out["views_per_upload"] == 100.0
    assert out["ctr"]["value"] == 4.5
