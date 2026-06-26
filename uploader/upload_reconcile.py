"""Reconcile stuck upload jobs — finalize, archive, or reset orphaned uploads."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from uploader.channel_list import list_channel_videos
from uploader.channels import AppConfig, ChannelConfig
from uploader.job_claim import release_job_claim
from uploader.job_lock import lock_is_expired_or_missing, release_upload_lock
from uploader.job_store import archive_job_from_entry
from uploader.job_views import detect_storage_folder
from uploader.oauth import OAuthSettings
from uploader.registry import (
    STATUS_UPLOADED,
    STATUS_UPLOADING,
    UploadEntry,
    UploadRegistry,
)


@dataclass
class ReconcileAction:
    channel_id: str
    job_id: str
    action: str
    detail: str = ""


@dataclass
class ReconcileResult:
    scanned: int = 0
    actions: list[ReconcileAction] = field(default_factory=list)

    def add(self, channel_id: str, job_id: str, action: str, detail: str = "") -> None:
        self.actions.append(
            ReconcileAction(channel_id=channel_id, job_id=job_id, action=action, detail=detail)
        )


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def reconcile_stale_seconds() -> int:
    """No progress update for this long → eligible for cleanup."""
    return _env_int("UPLOADER_RECONCILE_STALE_SECONDS", 180)


def reconcile_fail_seconds() -> int:
    """Uploading this long with no recovery → mark failed."""
    return _env_int("UPLOADER_RECONCILE_FAIL_SECONDS", 7200)


def _progress_age_seconds(entry: UploadEntry) -> float | None:
    extra = entry.extra or {}
    updated = extra.get("upload_updated_at") or extra.get("upload_started_at") or ""
    if not updated:
        return None
    try:
        ts = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - ts).total_seconds()


def _looks_complete(entry: UploadEntry) -> bool:
    extra = entry.extra or {}
    phase = str(extra.get("upload_phase", "") or "")
    progress = float(extra.get("upload_progress", 0) or 0)
    if phase == "done" or progress >= 99.0:
        return True
    if phase in ("archiving", "thumbnail") and progress >= 95.0:
        return True
    if phase == "uploading" and progress >= 93.0:
        msg = str(extra.get("upload_message", "") or "").lower()
        if "finished" in msg or "complete" in msg:
            return True
    return False


def _resolve_title(entry: UploadEntry, *, base: Path, channel: ChannelConfig) -> str:
    if entry.title:
        return entry.title.strip()
    try:
        from uploader.job_metadata import load_job_metadata

        meta = load_job_metadata(entry, base=base, channel=channel)
        if meta and meta.title:
            return meta.title.strip()
    except Exception:
        pass
    return ""


def _youtube_ids_in_use(registry: UploadRegistry) -> set[str]:
    ids: set[str] = set()
    for row in registry.load():
        if row.youtube_id:
            ids.add(row.youtube_id)
    return ids


def _match_youtube_video(
    title: str,
    videos: list,
    *,
    exclude_ids: set[str],
) -> str | None:
    if not title:
        return None
    needle = title.casefold()
    matches = [
        v
        for v in videos
        if v.video_id and v.video_id not in exclude_ids and (v.title or "").casefold() == needle
    ]
    if len(matches) == 1:
        return matches[0].video_id
    return None


def _finalize_upload(
    registry: UploadRegistry,
    channel: ChannelConfig,
    entry: UploadEntry,
    *,
    base: Path,
    youtube_id: str,
    dry_run: bool,
    result: ReconcileResult,
) -> bool:
    publish_at = entry.publish_at or ""
    if dry_run:
        result.add(
            channel.id,
            entry.id,
            "would_finalize",
            f"youtube_id={youtube_id} publish_at={publish_at or '(immediate)'}",
        )
        return True

    registry.mark_uploaded(entry.id, youtube_id=youtube_id, publish_at=publish_at)
    folder = detect_storage_folder(
        channel.id, entry.id, base=base, status=STATUS_UPLOADING, video_uri=entry.video_uri
    )
    if folder == "queue":
        try:
            refreshed = registry.get(entry.id) or entry
            archive_job_from_entry(refreshed, base=base, registry=registry)
        except Exception as e:
            result.add(channel.id, entry.id, "finalized_archive_failed", str(e))
            release_upload_lock(channel.id, entry.id, base=base, worker_id=None)
            return True
    release_upload_lock(channel.id, entry.id, base=base, worker_id=None)
    result.add(channel.id, entry.id, "finalized", youtube_id)
    return True


def _reconcile_uploading_entry(
    channel: ChannelConfig,
    entry: UploadEntry,
    *,
    base: Path,
    registry: UploadRegistry,
    oauth: OAuthSettings,
    channel_videos: list | None,
    dry_run: bool,
    result: ReconcileResult,
) -> None:
    age = _progress_age_seconds(entry)
    stale_sec = reconcile_stale_seconds()
    fail_sec = reconcile_fail_seconds()
    lock_free = lock_is_expired_or_missing(channel.id, entry.id, base=base)
    complete = _looks_complete(entry)
    folder = detect_storage_folder(
        channel.id, entry.id, base=base, status=STATUS_UPLOADING, video_uri=entry.video_uri
    )

    if entry.youtube_id:
        _finalize_upload(
            registry, channel, entry, base=base, youtube_id=entry.youtube_id, dry_run=dry_run, result=result
        )
        return

    if folder == "uploaded":
        yt_id = entry.youtube_id
        if not yt_id and channel_videos:
            title = _resolve_title(entry, base=base, channel=channel)
            yt_id = _match_youtube_video(
                title, channel_videos, exclude_ids=_youtube_ids_in_use(registry)
            ) or ""
        if yt_id:
            _finalize_upload(
                registry, channel, entry, base=base, youtube_id=yt_id, dry_run=dry_run, result=result
            )
        elif dry_run:
            result.add(channel.id, entry.id, "would_finalize", "storage already in uploaded/")
        elif not dry_run:
            registry.mark_uploaded(entry.id, youtube_id="", publish_at=entry.publish_at or "")
            release_upload_lock(channel.id, entry.id, base=base, worker_id=None)
            result.add(channel.id, entry.id, "marked_uploaded_no_yt_id", "assets in uploaded/")
        return

    should_act = lock_free and (
        (age is not None and age >= stale_sec)
        or (complete and age is not None and age >= 60)
    )
    if not should_act:
        if lock_free and age is not None and age >= stale_sec and dry_run:
            result.add(channel.id, entry.id, "would_check", f"stale {int(age)}s")
        return

    yt_id = ""
    if channel_videos is None and not dry_run:
        try:
            channel_videos = list_channel_videos(
                channel.token_path,
                client_secret=oauth.client_secret_path,
                client_config=oauth.client_config,
                oauth_port=oauth.oauth_port,
            )
        except Exception as e:
            result.add(channel.id, entry.id, "youtube_lookup_failed", str(e))
            channel_videos = []

    if channel_videos:
        title = _resolve_title(entry, base=base, channel=channel)
        yt_id = _match_youtube_video(
            title, channel_videos, exclude_ids=_youtube_ids_in_use(registry)
        ) or ""

    if yt_id:
        _finalize_upload(
            registry, channel, entry, base=base, youtube_id=yt_id, dry_run=dry_run, result=result
        )
        return

    if age is not None and age >= fail_sec:
        if dry_run:
            result.add(channel.id, entry.id, "would_fail", f"stale {int(age)}s, no YouTube match")
            return
        release_upload_lock(channel.id, entry.id, base=base, worker_id=None)
        registry.mark_failed(
            entry.id,
            error=f"Upload reconcile: no progress for {int(age)}s (worker likely died)",
        )
        result.add(channel.id, entry.id, "failed", f"stale {int(age)}s")
        return

    if dry_run:
        result.add(
            channel.id,
            entry.id,
            "would_reset_pending",
            f"stale {int(age or 0)}s, lock free, no YouTube match",
        )
        return

    release_job_claim(
        registry,
        channel.id,
        entry.id,
        str((entry.extra or {}).get("upload_worker_id", "") or ""),
        base=base,
        reset_to_pending=True,
    )
    release_upload_lock(channel.id, entry.id, base=base, worker_id=None)
    result.add(channel.id, entry.id, "reset_pending", f"stale {int(age or 0)}s")


def _reconcile_uploaded_entry(
    channel: ChannelConfig,
    entry: UploadEntry,
    *,
    base: Path,
    registry: UploadRegistry,
    dry_run: bool,
    result: ReconcileResult,
) -> None:
    folder = detect_storage_folder(
        channel.id, entry.id, base=base, status=STATUS_UPLOADED, video_uri=entry.video_uri
    )
    if folder != "queue":
        return
    if dry_run:
        result.add(channel.id, entry.id, "would_archive", "uploaded in registry but still in queue/")
        return
    try:
        archive_job_from_entry(entry, base=base, registry=registry)
        result.add(channel.id, entry.id, "archived", "moved queue/ → uploaded/")
    except Exception as e:
        result.add(channel.id, entry.id, "archive_failed", str(e))


def reconcile_uploads(
    config: AppConfig,
    *,
    base: Path,
    oauth: OAuthSettings,
    channel_id: str | None = None,
    dry_run: bool = False,
) -> ReconcileResult:
    """Scan registries and repair stuck uploading / un-archived uploaded jobs."""
    result = ReconcileResult()
    channels = config.channels
    if channel_id is not None:
        channels = [ch for ch in channels if ch.id == channel_id]
        if not channels:
            raise KeyError(f"Channel not found: {channel_id}")

    for channel in channels:
        registry = UploadRegistry(channel.registry_path)
        uploading = registry.uploading(channel_id=channel.id)
        uploaded = [e for e in registry.load() if e.status == STATUS_UPLOADED and e.channel_id == channel.id]

        result.scanned += len(uploading) + len(
            [e for e in uploaded if detect_storage_folder(
                channel.id, e.id, base=base, status=STATUS_UPLOADED, video_uri=e.video_uri
            ) == "queue"]
        )

        channel_videos = None
        needs_youtube = any(
            _looks_complete(e) or detect_storage_folder(
                channel.id, e.id, base=base, status=STATUS_UPLOADING, video_uri=e.video_uri
            ) == "uploaded"
            for e in uploading
        )
        if needs_youtube and not dry_run:
            try:
                channel_videos = list_channel_videos(
                    channel.token_path,
                    client_secret=oauth.client_secret_path,
                    client_config=oauth.client_config,
                    oauth_port=oauth.oauth_port,
                )
            except Exception:
                channel_videos = []

        for entry in uploading:
            _reconcile_uploading_entry(
                channel,
                entry,
                base=base,
                registry=registry,
                oauth=oauth,
                channel_videos=channel_videos,
                dry_run=dry_run,
                result=result,
            )

        for entry in uploaded:
            _reconcile_uploaded_entry(
                channel, entry, base=base, registry=registry, dry_run=dry_run, result=result
            )

    if not dry_run and result.actions:
        from uploader.cache_signals import bump

        bump("queue")

    return result
