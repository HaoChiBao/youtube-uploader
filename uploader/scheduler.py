"""Batch upload scheduler — process pending registry entries for a channel."""

from __future__ import annotations

import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from uploader.channels import AppConfig, ChannelConfig, get_channel
from uploader.progress import MultiProgress
from uploader.registry import UploadEntry, UploadRegistry
from uploader.storage import load_description, resolve_to_local_path
from uploader.job_metadata import load_job_metadata
from uploader.job_store import archive_job_from_entry
from uploader.oauth import resolve_oauth_settings
from uploader.state_store import config_base_from_path
from uploader.youtube_client import upload_video_with_retry


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
    no_schedule: bool = False,
    privacy: str | None = None,
    upload_retries: int = 3,
    retry_delay: float = 30.0,
    tags: list[str] | None = None,
) -> RunResult:
    """Process all pending uploads for a channel."""
    channel = get_channel(config, channel_id)
    registry = UploadRegistry(channel.registry_path)
    pending = registry.pending(channel_id=channel.id)
    if limit is not None:
        pending = pending[: max(0, limit)]

    result = RunResult(channel_id=channel.id, total=len(pending))
    if not pending:
        return result

    ivl = interval_hours if interval_hours is not None else channel.publish.interval_hours
    start_dt = parse_start(
        start,
        timezone_name=channel.publish.timezone,
        default_hour=channel.publish.hour,
    )
    plan = compute_publish_schedule(pending, start_dt, ivl, no_schedule=no_schedule)

    if dry_run:
        return result

    oauth = resolve_oauth_settings(
        config.google.client_secret_path,
        oauth_port=config.google.oauth_port,
    )
    token_path = channel.token_path
    import os

    config_path = Path(os.environ.get("UPLOADER_CONFIG", "config/channels.yaml")).expanduser().resolve()
    base = config_base_from_path(config_path)

    labels = [entry.id for entry, _ in plan]
    results: dict[int, tuple[str, str]] = {}

    with MultiProgress(labels) as bars:
        for i, (entry, publish_at) in enumerate(plan):
            registry.mark_uploading(entry.id)
            tmp_root: Path | None = None
            try:
                tmp_root = Path(tempfile.mkdtemp(prefix=f"uploader_{entry.id}_"))
                video_uri = entry.resolved_video_uri()
                if not video_uri:
                    raise FileNotFoundError("No video_uri or video path on entry")

                video_path = resolve_to_local_path(video_uri, temp_dir=tmp_root)
                job_meta = load_job_metadata(
                    entry, base=base, channel=channel, config_defaults=config.job_defaults
                )
                if job_meta:
                    description = job_meta.description or load_description(entry.description)
                    title = job_meta.title or entry.title or entry.id
                    effective_privacy = privacy if privacy is not None else job_meta.privacy
                    effective_category = job_meta.category_id or channel.category_id
                    effective_made_for_kids = job_meta.made_for_kids
                    effective_tags = (
                        tags
                        if tags is not None
                        else (job_meta.effective_tags() or channel.default_tags)
                    )
                else:
                    description = load_description(entry.description)
                    title = entry.title or entry.id
                    effective_privacy = privacy or "private"
                    effective_category = channel.category_id
                    effective_made_for_kids = channel.made_for_kids
                    effective_tags = tags if tags is not None else channel.default_tags

                thumb_path = None
                thumb_uri = entry.resolved_thumbnail_uri()
                if thumb_uri:
                    try:
                        thumb_path = resolve_to_local_path(thumb_uri, temp_dir=tmp_root)
                    except (FileNotFoundError, ValueError):
                        thumb_path = None

                bars.update(i, 0.0, "uploading…")

                def on_progress(p: float, *, idx: int = i) -> None:
                    bars.update(idx, p * 100.0, "uploading…")

                def on_retry(
                    attempt: int, attempts: int, err: BaseException, *, idx: int = i
                ) -> None:
                    bars.update(idx, 0.0, f"retry {attempt}/{attempts} ({err})…")

                response = upload_video_with_retry(
                    video_path,
                    max_attempts=upload_retries,
                    retry_delay_sec=retry_delay,
                    on_retry=on_retry,
                    title=title,
                    description=description,
                    client_secret=oauth.client_secret_path,
                    client_config=oauth.client_config,
                    token_path=token_path,
                    privacy=effective_privacy,
                    category_id=effective_category,
                    tags=effective_tags or None,
                    made_for_kids=effective_made_for_kids,
                    thumbnail_path=thumb_path,
                    publish_at=publish_at or None,
                    oauth_port=oauth.oauth_port,
                    on_progress=on_progress,
                )

                youtube_id = response.get("id", "")
                registry.mark_uploaded(entry.id, youtube_id=youtube_id, publish_at=publish_at)
                try:
                    archive_job_from_entry(entry, base=base, registry=registry)
                except Exception as archive_err:
                    print(
                        f"  warning: could not archive {entry.id} to uploaded/: {archive_err}",
                        file=sys.stderr,
                    )
                result.uploaded += 1
                url = f"https://youtu.be/{youtube_id}"
                result.urls.append(url)
                when = "immediately" if no_schedule else f"scheduled {publish_at}"
                msg = "done"
                if response.get("_thumbnail_warning"):
                    msg = "done (thumbnail skipped)"
                bars.update(i, 100.0, msg)
                results[i] = (youtube_id, when)

            except Exception as e:
                registry.mark_failed(entry.id, error=str(e))
                result.failed += 1
                result.errors.append((entry.id, str(e)))
                bars.update(i, bars.pcts[i], f"FAILED: {e}")
            finally:
                if tmp_root and tmp_root.exists():
                    shutil.rmtree(tmp_root, ignore_errors=True)

    print(file=sys.stderr)
    for i, (entry, _) in enumerate(plan):
        if i in results:
            youtube_id, when = results[i]
            print(f"  {entry.id}: https://youtu.be/{youtube_id}  ({when})", file=sys.stderr)

    return result


def run_all_channels(
    config: AppConfig,
    *,
    dry_run: bool = False,
    start: str | None = None,
    interval_hours: float | None = None,
    limit: int | None = None,
    no_schedule: bool = False,
    privacy: str | None = None,
    upload_retries: int = 3,
    retry_delay: float = 30.0,
    tags: list[str] | None = None,
) -> list[RunResult]:
    """Process pending uploads for every configured channel."""
    results: list[RunResult] = []
    for channel in config.channels:
        result = run_channel(
            channel.id,
            config,
            dry_run=dry_run,
            start=start,
            interval_hours=interval_hours,
            limit=limit,
            no_schedule=no_schedule,
            privacy=privacy,
            upload_retries=upload_retries,
            retry_delay=retry_delay,
            tags=tags,
        )
        results.append(result)
    return results
