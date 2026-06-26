"""Upload a single queued job (used by scheduler and Cloud Run workers)."""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from uploader.channels import AppConfig, ChannelConfig, get_channel
from uploader.job_claim import release_job_claim, try_claim_job
from uploader.job_lock import refresh_upload_lock, release_upload_lock
from uploader.job_metadata import load_job_metadata
from uploader.job_store import archive_job_from_entry
from uploader.oauth import resolve_oauth_settings
from uploader.registry import UploadEntry, UploadRegistry
from uploader.state_store import config_base_from_path
from uploader.storage import load_description, resolve_to_local_path
from uploader.youtube_client import upload_video_with_retry


@dataclass
class SingleUploadResult:
    channel_id: str
    job_id: str
    success: bool
    youtube_id: str = ""
    youtube_url: str = ""
    publish_at: str = ""
    error: str = ""
    worker_id: str = ""


class _ProgressReporter:
    def __init__(
        self,
        registry: UploadRegistry,
        job_id: str,
        *,
        min_interval: float = 0.4,
    ) -> None:
        self._registry = registry
        self._job_id = job_id
        self._min_interval = min_interval
        self._last_at = 0.0
        self._last_pct = -1.0
        self._last_phase = ""

    def set(self, phase: str, progress: float, message: str = "") -> None:
        now = time.monotonic()
        pct = max(0.0, min(100.0, float(progress)))
        phase_changed = phase != self._last_phase
        force = phase_changed or pct >= 100.0 or phase == "done"
        if (
            not force
            and now - self._last_at < self._min_interval
            and abs(pct - self._last_pct) < 1.0
        ):
            return
        self._last_at = now
        self._last_pct = pct
        self._last_phase = phase
        self._registry.set_upload_progress(
            self._job_id, phase=phase, progress=pct, message=message
        )


def upload_single_job(
    channel_id: str,
    job_id: str,
    config: AppConfig,
    *,
    worker_id: str,
    base: Path | None = None,
    publish_at: str | None = None,
    no_schedule: bool = False,
    privacy: str | None = None,
    upload_retries: int = 3,
    retry_delay: float = 30.0,
    tags: list[str] | None = None,
    require_claim: bool = True,
    lease_seconds: int = 7200,
) -> SingleUploadResult:
    """Download one job from storage and upload to YouTube."""
    import os

    channel = get_channel(config, channel_id)
    registry = UploadRegistry(channel.registry_path)
    config_path = Path(os.environ.get("UPLOADER_CONFIG", "config/channels.yaml")).expanduser().resolve()
    if base is None:
        base = config_base_from_path(config_path)

    claim = try_claim_job(
        registry,
        channel.id,
        job_id,
        worker_id,
        base=base,
        publish_at=publish_at or "",
        lease_seconds=lease_seconds,
    )
    if require_claim and not claim.claimed:
        return SingleUploadResult(
            channel_id=channel.id,
            job_id=job_id,
            success=False,
            error=claim.reason or "could not claim job",
            worker_id=worker_id,
        )

    entry = registry.get(job_id)
    if entry is None:
        release_upload_lock(channel.id, job_id, base=base, worker_id=worker_id)
        return SingleUploadResult(
            channel_id=channel.id,
            job_id=job_id,
            success=False,
            error="job not found after claim",
            worker_id=worker_id,
        )

    effective_publish_at = ""
    if not no_schedule:
        effective_publish_at = publish_at or entry.publish_at or ""

    progress = _ProgressReporter(registry, job_id)
    tmp_root: Path | None = None
    try:
        progress.set("preparing", 2, "Preparing upload")
        refresh_upload_lock(channel.id, job_id, worker_id, base=base, lease_seconds=lease_seconds)

        tmp_root = Path(tempfile.mkdtemp(prefix=f"uploader_{job_id}_"))
        video_uri = entry.resolved_video_uri()
        if not video_uri:
            raise FileNotFoundError("No video_uri or video path on entry")

        downloaded = [0]

        def on_download(bytes_amount: int) -> None:
            downloaded[0] += bytes_amount
            progress.set("downloading", min(24.0, 5.0 + downloaded[0] / (50 * 1024 * 1024)), "Downloading from storage")

        progress.set("downloading", 5, "Downloading video from storage")
        video_path = _resolve_video_with_progress(video_uri, temp_dir=tmp_root, on_bytes=on_download)

        job_meta = load_job_metadata(entry, base=base, channel=channel, config_defaults=config.job_defaults)
        title, description, effective_privacy, effective_category, effective_made_for_kids, effective_tags = (
            _resolve_metadata(
                entry,
                channel,
                config,
                job_meta,
                privacy=privacy,
                tags=tags,
            )
        )

        thumb_path = None
        thumb_uri = entry.resolved_thumbnail_uri()
        if thumb_uri:
            try:
                progress.set("downloading", 22, "Downloading thumbnail")
                thumb_path = resolve_to_local_path(thumb_uri, temp_dir=tmp_root)
            except (FileNotFoundError, ValueError):
                thumb_path = None

        oauth = resolve_oauth_settings(
            config.google.client_secret_path,
            oauth_port=config.google.oauth_port,
        )

        def on_yt_progress(p: float) -> None:
            pct = 25.0 + p * 68.0
            progress.set("uploading", pct, f"Uploading to YouTube ({int(p * 100)}%)")

        def on_retry(attempt: int, attempts: int, err: BaseException) -> None:
            progress.set("uploading", 25.0, f"Retry {attempt}/{attempts}: {err}")

        progress.set("uploading", 25, "Uploading to YouTube")
        refresh_upload_lock(channel.id, job_id, worker_id, base=base, lease_seconds=lease_seconds)

        response = upload_video_with_retry(
            video_path,
            max_attempts=upload_retries,
            retry_delay_sec=retry_delay,
            on_retry=on_retry,
            title=title,
            description=description,
            client_secret=oauth.client_secret_path,
            client_config=oauth.client_config,
            token_path=channel.token_path,
            privacy=effective_privacy,
            category_id=effective_category,
            tags=effective_tags or None,
            made_for_kids=effective_made_for_kids,
            thumbnail_path=thumb_path,
            publish_at=effective_publish_at or None,
            oauth_port=oauth.oauth_port,
            on_progress=on_yt_progress,
        )
        progress.set("uploading", 93, "YouTube upload finished")

        if thumb_path and response.get("_thumbnail_warning"):
            progress.set("thumbnail", 96, "Thumbnail skipped")
        elif thumb_path:
            progress.set("thumbnail", 96, "Thumbnail uploaded")

        youtube_id = response.get("id", "")
        # Persist uploaded status before thumbnail/archive so a worker crash cannot leave
        # the job stuck in uploading at 100%.
        registry.mark_uploaded(job_id, youtube_id=youtube_id, publish_at=effective_publish_at)
        progress.set("archiving", 98, "Archiving job")
        try:
            archive_job_from_entry(entry, base=base, registry=registry)
        except Exception as archive_err:
            print(f"  warning: could not archive {job_id}: {archive_err}", file=sys.stderr)

        progress.set("done", 100, "Upload complete")
        release_upload_lock(channel.id, job_id, base=base, worker_id=worker_id)
        url = f"https://youtu.be/{youtube_id}" if youtube_id else ""
        return SingleUploadResult(
            channel_id=channel.id,
            job_id=job_id,
            success=True,
            youtube_id=youtube_id,
            youtube_url=url,
            publish_at=effective_publish_at,
            worker_id=worker_id,
        )

    except Exception as e:
        existing = registry.get(job_id)
        if existing and existing.status == "uploaded":
            release_upload_lock(channel.id, job_id, base=base, worker_id=worker_id)
            return SingleUploadResult(
                channel_id=channel.id,
                job_id=job_id,
                success=True,
                youtube_id=existing.youtube_id,
                youtube_url=existing.youtube_url,
                publish_at=existing.publish_at,
                worker_id=worker_id,
            )
        registry.mark_failed(job_id, error=str(e))
        release_upload_lock(channel.id, job_id, base=base, worker_id=worker_id)
        return SingleUploadResult(
            channel_id=channel.id,
            job_id=job_id,
            success=False,
            error=str(e),
            worker_id=worker_id,
        )
    finally:
        if tmp_root and tmp_root.exists():
            shutil.rmtree(tmp_root, ignore_errors=True)


def _resolve_video_with_progress(uri: str, *, temp_dir: Path, on_bytes) -> Path:
    from urllib.parse import urlparse

    from uploader.object_storage import is_s3_uri, parse_s3_uri, _s3_client_for_uri

    temp_dir.mkdir(parents=True, exist_ok=True)
    if not is_s3_uri(uri):
        return resolve_to_local_path(uri, temp_dir=temp_dir)

    parsed = urlparse(uri)
    if parsed.scheme == "s3":
        bucket, key = parse_s3_uri(uri)
        filename = Path(key).name or "download"
        dest = temp_dir / filename
        client = _s3_client_for_uri(uri)

        def callback(amount: int) -> None:
            if on_bytes:
                on_bytes(amount)

        client.download_file(bucket, key, str(dest), Callback=callback)
        return dest
    return resolve_to_local_path(uri, temp_dir=temp_dir)


def _resolve_metadata(
    entry: UploadEntry,
    channel: ChannelConfig,
    config: AppConfig,
    job_meta,
    *,
    privacy: str | None,
    tags: list[str] | None,
) -> tuple[str, str, str, str, bool, list[str] | None]:
    if job_meta:
        description = job_meta.description or load_description(entry.description)
        title = job_meta.title or entry.title or entry.id
        effective_privacy = privacy if privacy is not None else job_meta.privacy
        effective_category = job_meta.category_id or channel.category_id
        effective_made_for_kids = job_meta.made_for_kids
        effective_tags = tags if tags is not None else (job_meta.effective_tags() or channel.default_tags)
    else:
        description = load_description(entry.description)
        title = entry.title or entry.id
        effective_privacy = privacy or "private"
        effective_category = channel.category_id
        effective_made_for_kids = channel.made_for_kids
        effective_tags = tags if tags is not None else channel.default_tags
    return title, description, effective_privacy, effective_category, effective_made_for_kids, effective_tags
