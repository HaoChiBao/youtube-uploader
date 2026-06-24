"""Batch upload scheduler — process pending registry entries for a channel."""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from uploader.channels import AppConfig, ChannelConfig, get_channel
from uploader.registry import UploadEntry, UploadRegistry
from uploader.state_store import config_base_from_path
from uploader.upload_worker import upload_single_job


@dataclass
class ChannelUploadPlan:
    """Publish plan for a batch of pending jobs."""

    items: list[tuple[UploadEntry, str]]
    upload_immediately: bool = False
    anchor: str = "default"  # immediate | youtube_tail | explicit_start | no_schedule | fallback_tomorrow
    interval_hours: float = 24.0


def effective_interval_hours(
    channel: ChannelConfig,
    scheduled: list[datetime] | None,
    interval_hours: float | None,
) -> float:
    """Resolve spacing between uploads: explicit > inferred from YouTube > channel default."""
    if interval_hours is not None:
        return interval_hours
    if scheduled and len(scheduled) >= 2:
        from uploader.channel_list import infer_schedule_interval_hours

        inferred = infer_schedule_interval_hours(scheduled)
        if inferred is not None:
            return inferred
    return channel.publish.interval_hours


def build_channel_upload_plan(
    channel: ChannelConfig,
    config: AppConfig,
    pending: list[UploadEntry],
    *,
    start: str | None = None,
    interval_hours: float | None = None,
    no_schedule: bool = False,
    oauth_client_secret: Path | None = None,
    oauth_client_config: dict | None = None,
    oauth_port: int | None = None,
) -> ChannelUploadPlan:
    """Plan upload times: append after YouTube schedule, or upload immediately if none."""
    scheduled: list[datetime] | None = None
    if not no_schedule and not start:
        try:
            from uploader.channel_list import fetch_scheduled_publish_datetimes

            scheduled = fetch_scheduled_publish_datetimes(
                channel.token_path,
                client_secret=oauth_client_secret,
                client_config=oauth_client_config,
                oauth_port=oauth_port if oauth_port is not None else config.google.oauth_port,
            )
        except Exception as e:
            print(
                f"  warning: could not read YouTube schedule for {channel.id}, "
                f"scheduling from tomorrow: {e}",
                file=sys.stderr,
            )

    ivl = effective_interval_hours(channel, scheduled, interval_hours)

    if no_schedule:
        start_dt = parse_start(
            start,
            timezone_name=channel.publish.timezone,
            default_hour=channel.publish.hour,
        )
        return ChannelUploadPlan(
            items=compute_publish_schedule(pending, start_dt, ivl, no_schedule=True),
            upload_immediately=True,
            anchor="no_schedule",
            interval_hours=ivl,
        )

    if start:
        start_dt = parse_start(
            start,
            timezone_name=channel.publish.timezone,
            default_hour=channel.publish.hour,
        )
        return ChannelUploadPlan(
            items=compute_publish_schedule(pending, start_dt, ivl, no_schedule=False),
            upload_immediately=False,
            anchor="explicit_start",
            interval_hours=ivl,
        )

    if scheduled is not None and not scheduled:
        return ChannelUploadPlan(
            items=[(entry, "") for entry in pending],
            upload_immediately=True,
            anchor="immediate",
            interval_hours=ivl,
        )

    if scheduled:
        start_dt = resolve_publish_start(
            channel,
            interval_hours=ivl,
            scheduled_publish_ats=scheduled,
        )
        return ChannelUploadPlan(
            items=compute_publish_schedule(pending, start_dt, ivl, no_schedule=False),
            upload_immediately=False,
            anchor="youtube_tail",
            interval_hours=ivl,
        )

    start_dt = parse_start(
        None,
        timezone_name=channel.publish.timezone,
        default_hour=channel.publish.hour,
    )
    return ChannelUploadPlan(
        items=compute_publish_schedule(pending, start_dt, ivl, no_schedule=False),
        upload_immediately=False,
        anchor="fallback_tomorrow",
        interval_hours=ivl,
    )


def resolve_publish_start(
    channel: ChannelConfig,
    *,
    start: str | None = None,
    interval_hours: float | None = None,
    no_schedule: bool = False,
    scheduled_publish_ats: list[datetime] | None = None,
) -> datetime:
    """First publishAt for the next batch.

    When `start` is not set and scheduling is enabled, anchor after the latest
    YouTube scheduled video (+ interval). Otherwise use tomorrow at publish.hour.
    """
    tz = channel.publish.timezone
    hour = channel.publish.hour
    ivl = interval_hours if interval_hours is not None else channel.publish.interval_hours

    if start:
        return parse_start(start, timezone_name=tz, default_hour=hour)
    if no_schedule:
        return parse_start(None, timezone_name=tz, default_hour=hour)
    if scheduled_publish_ats:
        tail = max(scheduled_publish_ats)
        if tail.tzinfo is None:
            tail = tail.replace(tzinfo=timezone.utc)
        return tail + timedelta(hours=ivl)
    return parse_start(None, timezone_name=tz, default_hour=hour)


def resolve_publish_start_for_channel(
    channel: ChannelConfig,
    config: AppConfig,
    *,
    start: str | None = None,
    interval_hours: float | None = None,
    no_schedule: bool = False,
    oauth_client_secret: Path | None = None,
    oauth_client_config: dict | None = None,
    oauth_port: int | None = None,
) -> datetime:
    """Resolve first publishAt for plan preview (legacy helper)."""
    plan = build_channel_upload_plan(
        channel,
        config,
        pending=[UploadEntry(id="_preview", channel_id=channel.id)],
        start=start,
        interval_hours=interval_hours,
        no_schedule=no_schedule,
        oauth_client_secret=oauth_client_secret,
        oauth_client_config=oauth_client_config,
        oauth_port=oauth_port,
    )
    if plan.upload_immediately:
        return parse_start(
            None,
            timezone_name=channel.publish.timezone,
            default_hour=channel.publish.hour,
        )
    _, publish_at = plan.items[0]
    if publish_at:
        return datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
    return parse_start(
        None,
        timezone_name=channel.publish.timezone,
        default_hour=channel.publish.hour,
    )


@dataclass
class RunResult:
    channel_id: str
    total: int = 0
    uploaded: int = 0
    failed: int = 0
    urls: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def to_rfc3339_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_start(
    value: str | None,
    *,
    timezone_name: str = "America/New_York",
    default_hour: int = 9,
) -> datetime:
    """Return a timezone-aware datetime for the first publish time."""
    tz = ZoneInfo(timezone_name)
    if not value:
        now_local = datetime.now(tz)
        tomorrow = (now_local + timedelta(days=1)).replace(
            hour=default_hour, minute=0, second=0, microsecond=0
        )
        return tomorrow

    text = value.strip().replace(" ", "T")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def compute_publish_schedule(
    pending: list[UploadEntry],
    start: datetime,
    interval_hours: float,
    *,
    no_schedule: bool = False,
) -> list[tuple[UploadEntry, str]]:
    """Return (entry, publish_at_rfc3339) pairs for each pending job."""
    plan: list[tuple[UploadEntry, str]] = []
    for i, entry in enumerate(pending):
        publish_dt = start + timedelta(hours=interval_hours * i)
        publish_at = "" if no_schedule else to_rfc3339_utc(publish_dt)
        plan.append((entry, publish_at))
    return plan


def run_channel(
    channel_id: str,
    config: AppConfig,
    *,
    dry_run: bool = False,
    start: str | None = None,
    interval_hours: float | None = None,
    limit: int | None = None,
    uploads_per_day: int | None = None,
    no_schedule: bool = False,
    privacy: str | None = None,
    upload_retries: int = 3,
    retry_delay: float = 30.0,
    tags: list[str] | None = None,
    oauth_client_secret: Path | None = None,
    oauth_client_config: dict | None = None,
    oauth_port: int | None = None,
) -> RunResult:
    """Process pending uploads sequentially in-process (one job at a time)."""
    import os

    channel = get_channel(config, channel_id)
    registry = UploadRegistry(channel.registry_path)
    pending = registry.pending(channel_id=channel.id)
    daily_cap = uploads_per_day if uploads_per_day is not None else channel.publish.uploads_per_day
    if daily_cap is not None:
        cap = daily_cap if limit is None else min(limit, daily_cap)
        pending = pending[: max(0, cap)]
    elif limit is not None:
        pending = pending[: max(0, limit)]

    result = RunResult(channel_id=channel.id, total=len(pending))
    if not pending:
        return result

    upload_plan = build_channel_upload_plan(
        channel,
        config,
        pending,
        start=start,
        interval_hours=interval_hours,
        no_schedule=no_schedule,
        oauth_client_secret=oauth_client_secret,
        oauth_client_config=oauth_client_config,
        oauth_port=oauth_port,
    )
    plan = upload_plan.items

    if dry_run:
        return result

    config_path = Path(os.environ.get("UPLOADER_CONFIG", "config/channels.yaml")).expanduser().resolve()
    base = config_base_from_path(config_path)

    for entry, publish_at in plan:
        worker_id = f"seq_{uuid.uuid4().hex[:10]}"
        one = upload_single_job(
            channel.id,
            entry.id,
            config,
            worker_id=worker_id,
            base=base,
            publish_at=publish_at,
            no_schedule=upload_plan.upload_immediately or not publish_at,
            privacy=privacy,
            upload_retries=upload_retries,
            retry_delay=retry_delay,
            tags=tags,
        )
        if one.success:
            result.uploaded += 1
            if one.youtube_url:
                result.urls.append(one.youtube_url)
            when = "immediately" if (upload_plan.upload_immediately or not publish_at) else f"scheduled {one.publish_at}"
            print(f"  {entry.id}: {one.youtube_url}  ({when})", file=sys.stderr)
        else:
            result.failed += 1
            result.errors.append((entry.id, one.error))
            print(f"  {entry.id}: FAILED — {one.error}", file=sys.stderr)

    return result


def run_all_channels(
    config: AppConfig,
    *,
    dry_run: bool = False,
    start: str | None = None,
    interval_hours: float | None = None,
    limit: int | None = None,
    uploads_per_day: int | None = None,
    no_schedule: bool = False,
    privacy: str | None = None,
    upload_retries: int = 3,
    retry_delay: float = 30.0,
    tags: list[str] | None = None,
    oauth_client_secret: Path | None = None,
    oauth_client_config: dict | None = None,
    oauth_port: int | None = None,
) -> list[RunResult]:
    """Process pending uploads for every configured channel."""
    results: list[RunResult] = []
    for channel in config.channels:
        results.append(
            run_channel(
                channel.id,
                config,
                dry_run=dry_run,
                start=start,
                interval_hours=interval_hours,
                limit=limit,
                uploads_per_day=uploads_per_day,
                no_schedule=no_schedule,
                privacy=privacy,
                upload_retries=upload_retries,
                retry_delay=retry_delay,
                tags=tags,
                oauth_client_secret=oauth_client_secret,
                oauth_client_config=oauth_client_config,
                oauth_port=oauth_port,
            )
        )
    return results
