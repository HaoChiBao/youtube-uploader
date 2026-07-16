"""Tests for the compact dashboard Today pulse."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.schemas import ChannelOut, DashboardResponse, JobOut, TokenStatus
from uploader.channel_list import YouTubeVideoInfo
from uploader.channels import AppConfig, ChannelConfig, GoogleConfig
from uploader.oauth import OAuthSettings
from uploader.today_pulse import build_today_pulse


def _channel_out() -> ChannelOut:
    return ChannelOut(
        id="channel-a",
        name="Channel A",
        token_path="token.json",
        registry_path="registry.txt",
        auth=TokenStatus(has_token=True, valid=True, status="ok"),
    )


def _dashboard() -> DashboardResponse:
    return DashboardResponse(
        config_uri="",
        storage="local",
        channels=[_channel_out()],
        queue_jobs=[
            JobOut(
                id="scheduled",
                channel_id="channel-a",
                status="pending",
                title="Later today",
                upload_at="2026-07-16T20:00:00Z",
            ),
            JobOut(
                id="tomorrow",
                channel_id="channel-a",
                status="pending",
                title="Tomorrow",
                upload_at="2026-07-17T20:00:00Z",
            ),
        ],
        uploaded_jobs=[
            JobOut(
                id="uploaded",
                channel_id="channel-a",
                status="uploaded",
                title="Already live",
                uploaded_at="2026-07-16T14:00:00Z",
                youtube_id="video-1",
                youtube_url="https://youtu.be/video-1",
            )
        ],
    )


def test_build_today_pulse_uploaded_scheduled_and_metrics() -> None:
    config = AppConfig(
        channels=[ChannelConfig(id="channel-a", name="Channel A")],
        google=GoogleConfig(),
    )
    oauth = OAuthSettings(None, None, 8080, "http://localhost:8080")

    def fetcher(token_path, video_ids, **kwargs):
        assert video_ids == ["video-1"]
        return {
            "video-1": YouTubeVideoInfo(
                video_id="video-1",
                title="Already live",
                privacy_status="public",
                publish_at=None,
                url="https://youtu.be/video-1",
                thumbnail_url="https://img.test/video-1.jpg",
                view_count=123,
                like_count=9,
                comment_count=2,
            )
        }

    result = build_today_pulse(
        config,
        oauth,
        _dashboard(),
        timezone_name="America/New_York",
        now=datetime(2026, 7, 16, 15, 0, tzinfo=timezone.utc),
        fetcher=fetcher,
    )

    assert result.date == "2026-07-16"
    assert result.uploaded_count == 1
    assert result.scheduled_count == 1
    assert result.views_so_far == 123
    assert result.likes_so_far == 9
    assert result.comments_so_far == 2
    assert result.metrics_available_count == 1
    assert [row.job_id for row in result.videos] == ["uploaded", "scheduled"]
    assert result.videos[0].thumbnail_url == "https://img.test/video-1.jpg"
    assert result.videos[1].schedule_kind == "upload_at"


def test_build_today_pulse_rejects_unknown_timezone() -> None:
    config = AppConfig(channels=[], google=GoogleConfig())
    oauth = OAuthSettings(None, None, 8080, "http://localhost:8080")
    with pytest.raises(ValueError, match="Unknown timezone"):
        build_today_pulse(config, oauth, _dashboard(), timezone_name="Mars/Olympus")
