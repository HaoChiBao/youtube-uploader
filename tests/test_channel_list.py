"""Tests for enriched YouTube video listing helpers."""

from __future__ import annotations

from uploader.channel_list import (
    YouTubeVideoInfo,
    parse_iso8601_duration,
    thumbnail_url_for_video,
)


def test_parse_iso8601_duration():
    assert parse_iso8601_duration("PT1H2M3S") == 3723
    assert parse_iso8601_duration("PT15M") == 900
    assert parse_iso8601_duration("PT45S") == 45
    assert parse_iso8601_duration("P1DT1H") == 90000
    assert parse_iso8601_duration("") is None
    assert parse_iso8601_duration("bogus") is None


def test_thumbnail_url_for_video_prefers_medium():
    thumbs = {
        "default": {"url": "https://i.ytimg.com/vi/x/default.jpg"},
        "medium": {"url": "https://i.ytimg.com/vi/x/mqdefault.jpg"},
        "high": {"url": "https://i.ytimg.com/vi/x/hqdefault.jpg"},
    }
    assert thumbnail_url_for_video("x", thumbs) == "https://i.ytimg.com/vi/x/mqdefault.jpg"


def test_thumbnail_url_fallback():
    assert thumbnail_url_for_video("abc123", None) == "https://i.ytimg.com/vi/abc123/hqdefault.jpg"


def test_is_scheduled_requires_future_private():
    v = YouTubeVideoInfo(
        video_id="v1",
        title="t",
        privacy_status="private",
        publish_at="2099-01-01T00:00:00Z",
        url="https://youtu.be/v1",
    )
    assert v.is_scheduled is True
    public = YouTubeVideoInfo(
        video_id="v2",
        title="t",
        privacy_status="public",
        publish_at="2099-01-01T00:00:00Z",
        url="https://youtu.be/v2",
    )
    assert public.is_scheduled is False
