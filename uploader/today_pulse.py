"""Build the compact dashboard pulse for today's uploads."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from api.schemas import DashboardResponse, TodayPulseResponse, TodayVideoOut
from uploader.channels import AppConfig
from uploader.oauth import OAuthSettings
from uploader.channel_list import YouTubeVideoInfo, get_channel_videos


def _parse_datetime(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _viewer_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone {name!r}") from exc


def _metadata_by_channel(
    config: AppConfig,
    oauth: OAuthSettings,
    ids_by_channel: dict[str, list[str]],
    *,
    fetcher: Callable[..., dict[str, YouTubeVideoInfo]],
) -> dict[str, YouTubeVideoInfo]:
    channels = {channel.id: channel for channel in config.channels}
    out: dict[str, YouTubeVideoInfo] = {}
    if not ids_by_channel:
        return out

    def fetch(channel_id: str) -> dict[str, YouTubeVideoInfo]:
        channel = channels[channel_id]
        return fetcher(
            channel.token_path,
            ids_by_channel[channel_id],
            client_secret=oauth.client_secret_path,
            client_config=oauth.client_config,
            oauth_port=oauth.oauth_port,
        )

    with ThreadPoolExecutor(max_workers=min(6, len(ids_by_channel))) as pool:
        futures = {
            pool.submit(fetch, channel_id): channel_id
            for channel_id in ids_by_channel
            if channel_id in channels
        }
        for future in as_completed(futures):
            try:
                out.update(future.result())
            except Exception:
                # The pulse remains useful when one channel is disconnected or
                # YouTube is temporarily unavailable; that row simply has no metrics.
                continue
    return out


def build_today_pulse(
    config: AppConfig,
    oauth: OAuthSettings,
    dashboard: DashboardResponse,
    *,
    timezone_name: str = "UTC",
    now: datetime | None = None,
    fetcher: Callable[..., dict[str, YouTubeVideoInfo]] = get_channel_videos,
) -> TodayPulseResponse:
    """Return uploaded and scheduled jobs for the viewer's current day."""
    viewer_tz = _viewer_timezone(timezone_name)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_now = current.astimezone(viewer_tz)
    today = local_now.date()
    channel_names = {channel.id: channel.name or channel.id for channel in dashboard.channels}

    uploaded_rows: list[TodayVideoOut] = []
    ids_by_channel: dict[str, list[str]] = {}
    for job in dashboard.uploaded_jobs:
        event = _parse_datetime(job.uploaded_at)
        if event is None or event.astimezone(viewer_tz).date() != today:
            continue
        row = TodayVideoOut(
            job_id=job.id,
            channel_id=job.channel_id,
            channel_name=channel_names.get(job.channel_id, job.channel_id),
            title=job.title or job.id,
            status="uploaded",
            event_at=event.isoformat().replace("+00:00", "Z"),
            schedule_kind="uploaded_at",
            video_id=job.youtube_id,
            youtube_url=job.youtube_url,
        )
        uploaded_rows.append(row)
        if job.youtube_id:
            ids_by_channel.setdefault(job.channel_id, []).append(job.youtube_id)

    scheduled_rows: list[TodayVideoOut] = []
    for job in dashboard.queue_jobs:
        if job.status != "pending":
            continue
        schedule_kind = "upload_at" if job.upload_at else "publish_at"
        event = _parse_datetime(job.upload_at or job.publish_at)
        if event is None or event.astimezone(viewer_tz).date() != today:
            continue
        scheduled_rows.append(
            TodayVideoOut(
                job_id=job.id,
                channel_id=job.channel_id,
                channel_name=channel_names.get(job.channel_id, job.channel_id),
                title=job.title or job.id,
                status="scheduled",
                event_at=event.isoformat().replace("+00:00", "Z"),
                schedule_kind=schedule_kind,
            )
        )

    metadata = _metadata_by_channel(config, oauth, ids_by_channel, fetcher=fetcher)
    metrics_available = 0
    for row in uploaded_rows:
        video = metadata.get(row.video_id)
        if video is None:
            continue
        row.thumbnail_url = video.thumbnail_url
        row.privacy_status = video.privacy_status
        row.youtube_url = video.url or row.youtube_url
        row.views = video.view_count
        row.likes = video.like_count
        row.comments = video.comment_count
        metrics_available += 1

    uploaded_rows.sort(key=lambda row: row.event_at, reverse=True)
    scheduled_rows.sort(key=lambda row: row.event_at)
    rows = uploaded_rows + scheduled_rows
    return TodayPulseResponse(
        date=today.isoformat(),
        timezone=timezone_name or "UTC",
        uploaded_count=len(uploaded_rows),
        scheduled_count=len(scheduled_rows),
        views_so_far=sum(row.views or 0 for row in uploaded_rows),
        likes_so_far=sum(row.likes or 0 for row in uploaded_rows),
        comments_so_far=sum(row.comments or 0 for row in uploaded_rows),
        metrics_available_count=metrics_available,
        refreshed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        videos=rows,
    )
