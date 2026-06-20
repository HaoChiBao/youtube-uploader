"""Shared job listing for CLI and API (queue vs uploaded, FIFO order, storage paths)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from uploader import bucket_layout
from uploader.channels import ChannelConfig
from uploader.job_store import _prefix_has_objects
from uploader.object_storage import exists, is_s3_uri, parse_s3_uri
from uploader.registry import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_UPLOADED,
    STATUS_UPLOADING,
    UploadEntry,
    UploadRegistry,
)

JobLocation = Literal["queue", "uploaded", "missing"]


@dataclass
class JobView:
    """API/CLI-ready job row with storage folder and queue ordering."""

    id: str
    channel_id: str
    status: str
    title: str = ""
    description: str = ""
    video_uri: str = ""
    thumbnail_uri: str = ""
    youtube_id: str = ""
    youtube_url: str = ""
    publish_at: str = ""
    created_at: str = ""
    uploaded_at: str = ""
    error: str = ""
    storage_folder: JobLocation = "missing"
    queue_position: int | None = None
    queue_prefix: str = ""
    uploaded_prefix: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "status": self.status,
            "title": self.title,
            "description": self.description,
            "video_uri": self.video_uri,
            "thumbnail_uri": self.thumbnail_uri,
            "youtube_id": self.youtube_id,
            "youtube_url": self.youtube_url,
            "publish_at": self.publish_at,
            "created_at": self.created_at,
            "uploaded_at": self.uploaded_at,
            "error": self.error,
            "storage_folder": self.storage_folder,
            "queue_position": self.queue_position,
            "queue_prefix": self.queue_prefix,
            "uploaded_prefix": self.uploaded_prefix,
        }


def queue_prefix_for(channel_id: str, job_id: str, base: Path) -> str:
    return bucket_layout.job_prefix_location(channel_id, job_id, base)


def uploaded_prefix_for(channel_id: str, job_id: str, base: Path) -> str:
    return bucket_layout.uploaded_prefix_location(channel_id, job_id, base)


def _infer_folder_from_uri(video_uri: str) -> JobLocation | None:
    """Infer queue/ vs uploaded/ from registry URI path (even if object moved)."""
    norm = video_uri.replace("\\", "/")
    if f"/{bucket_layout.UPLOADED_PREFIX}/" in norm or "/archive/" in norm:
        return "uploaded"
    if f"/{bucket_layout.QUEUE_PREFIX}/" in norm or "/videos/" in norm:
        return "queue"
    return None


def _prefix_location_from_key(key_prefix: str, base: Path) -> str:
    return bucket_layout.prefix_location(key_prefix, base)


def _first_existing_prefix(key_prefixes: tuple[str, ...], base: Path) -> str | None:
    for key_prefix in key_prefixes:
        loc = _prefix_location_from_key(key_prefix, base)
        if _prefix_has_objects(loc):
            return loc
    return None


def detect_storage_folder(
    channel_id: str,
    job_id: str,
    *,
    base: Path,
    status: str,
    video_uri: str = "",
) -> JobLocation:
    """Return where job assets live: queue/, uploaded/, or missing."""
    if video_uri:
        inferred = _infer_folder_from_uri(video_uri)
        if exists(video_uri):
            return inferred or ("uploaded" if status == STATUS_UPLOADED else "queue")
        if inferred:
            return inferred

    uploaded_loc = _first_existing_prefix(
        bucket_layout.uploaded_prefix_candidates(channel_id, job_id), base
    )
    queue_loc = _first_existing_prefix(bucket_layout.queue_prefix_candidates(channel_id, job_id), base)

    if status == STATUS_UPLOADED:
        if uploaded_loc:
            return "uploaded"
        if queue_loc:
            return "queue"
        if video_uri and (inferred := _infer_folder_from_uri(video_uri)):
            return inferred
        return "missing"

    if queue_loc:
        return "queue"
    if uploaded_loc:
        return "uploaded"
    if video_uri and (inferred := _infer_folder_from_uri(video_uri)):
        return inferred
    if status in (STATUS_PENDING, STATUS_UPLOADING, STATUS_FAILED):
        return "queue"
    return "missing"


def job_folder_prefix(entry: UploadEntry, *, base: Path, folder: JobLocation) -> str:
    """Best-effort folder prefix for display (actual location if found, else canonical)."""
    if folder == "uploaded":
        found = _first_existing_prefix(
            bucket_layout.uploaded_prefix_candidates(entry.channel_id, entry.id), base
        )
        if found:
            return found
    else:
        found = _first_existing_prefix(
            bucket_layout.queue_prefix_candidates(entry.channel_id, entry.id), base
        )
        if found:
            return found

    video = entry.resolved_video_uri()
    if video:
        if is_s3_uri(video):
            bucket, key = parse_s3_uri(video)
            if "/" in key:
                return f"s3://{bucket}/{'/'.join(key.split('/')[:-1])}/"
        else:
            from pathlib import Path as P

            p = P(video)
            parent = p.parent if p.is_file() else p
            return str(parent.resolve()) + "/"

    if folder == "uploaded":
        return uploaded_prefix_for(entry.channel_id, entry.id, base)
    return queue_prefix_for(entry.channel_id, entry.id, base)


def resolve_job_asset_uri(
    entry: UploadEntry,
    filename: str,
    *,
    base: Path,
    folder: JobLocation | None = None,
) -> str:
    """Resolve video/thumbnail URI from registry paths or canonical layout."""
    if filename == bucket_layout.JOB_VIDEO:
        uri = entry.resolved_video_uri()
        if uri and exists(uri):
            return uri
    elif filename == bucket_layout.JOB_THUMBNAIL:
        uri = entry.resolved_thumbnail_uri()
        if uri and exists(uri):
            return uri

    loc = folder or detect_storage_folder(
        entry.channel_id, entry.id, base=base, status=entry.status, video_uri=entry.resolved_video_uri()
    )
    candidates = (
        bucket_layout.uploaded_prefix_candidates(entry.channel_id, entry.id)
        if loc == "uploaded"
        else bucket_layout.queue_prefix_candidates(entry.channel_id, entry.id)
    )
    for key_prefix in candidates:
        uri = bucket_layout.resolve_location(f"{key_prefix.rstrip('/')}/{filename}", base)
        if exists(uri):
            return uri
    if loc == "uploaded":
        return bucket_layout.uploaded_location(entry.channel_id, entry.id, filename, base)
    return bucket_layout.job_location(entry.channel_id, entry.id, filename, base)


def uploaded_uris_for_job(channel_id: str, job_id: str, base: Path) -> dict[str, str]:
    """Canonical uploaded/ URIs after archive."""
    return {
        "video_uri": bucket_layout.uploaded_location(channel_id, job_id, bucket_layout.JOB_VIDEO, base),
        "thumbnail_uri": bucket_layout.uploaded_location(
            channel_id, job_id, bucket_layout.JOB_THUMBNAIL, base
        ),
    }


def entry_to_job_view(
    entry: UploadEntry,
    *,
    base: Path,
    queue_position: int | None = None,
) -> JobView:
    folder = detect_storage_folder(
        entry.channel_id, entry.id, base=base, status=entry.status, video_uri=entry.resolved_video_uri()
    )
    prefix = job_folder_prefix(entry, base=base, folder=folder)
    return JobView(
        id=entry.id,
        channel_id=entry.channel_id,
        status=entry.status,
        title=entry.title,
        description=entry.description,
        video_uri=entry.resolved_video_uri(),
        thumbnail_uri=entry.resolved_thumbnail_uri(),
        youtube_id=entry.youtube_id,
        youtube_url=entry.youtube_url,
        publish_at=entry.publish_at,
        created_at=entry.created_at,
        uploaded_at=entry.uploaded_at,
        error=entry.error,
        storage_folder=folder,
        queue_position=queue_position,
        queue_prefix=prefix,
        uploaded_prefix=uploaded_prefix_for(entry.channel_id, entry.id, base),
    )


def job_media_availability(
    entry: UploadEntry,
    *,
    base: Path,
    folder: JobLocation | None = None,
) -> dict[str, bool]:
    loc = folder or detect_storage_folder(
        entry.channel_id, entry.id, base=base, status=entry.status, video_uri=entry.resolved_video_uri()
    )
    video_uri = resolve_job_asset_uri(entry, bucket_layout.JOB_VIDEO, base=base, folder=loc)
    thumb_uri = resolve_job_asset_uri(entry, bucket_layout.JOB_THUMBNAIL, base=base, folder=loc)
    return {
        "video": bool(video_uri and exists(video_uri)),
        "thumbnail": bool(thumb_uri and exists(thumb_uri)),
    }


@dataclass
class ChannelJobsBundle:
    channel: ChannelConfig
    queue_jobs: list[JobView]
    uploaded_jobs: list[JobView]
    pending_count: int
    uploaded_count: int
    failed_count: int


def load_channel_jobs(
    channel: ChannelConfig,
    *,
    base: Path,
) -> ChannelJobsBundle:
    """Load queue (FIFO pending) and uploaded history for one channel."""
    reg = UploadRegistry(channel.registry_path)
    all_entries = [e for e in reg.load() if e.channel_id == channel.id]

    pending = [e for e in all_entries if e.status == STATUS_PENDING]
    failed = [e for e in all_entries if e.status == STATUS_FAILED]
    uploading = [e for e in all_entries if e.status == STATUS_UPLOADING]
    uploaded = [e for e in all_entries if e.status == STATUS_UPLOADED]

    queue_entries: list[UploadEntry] = []
    queue_entries.extend(pending)
    queue_entries.extend(uploading)
    queue_entries.extend(failed)

    queue_jobs: list[JobView] = []
    for i, entry in enumerate(pending, start=1):
        view = entry_to_job_view(entry, base=base, queue_position=i)
        queue_jobs.append(view)
    for entry in uploading:
        view = entry_to_job_view(entry, base=base, queue_position=None)
        queue_jobs.append(view)
    for entry in failed:
        view = entry_to_job_view(entry, base=base, queue_position=None)
        queue_jobs.append(view)

    uploaded.sort(key=lambda e: e.uploaded_at or e.created_at, reverse=True)
    uploaded_jobs = [entry_to_job_view(e, base=base) for e in uploaded]

    return ChannelJobsBundle(
        channel=channel,
        queue_jobs=queue_jobs,
        uploaded_jobs=uploaded_jobs,
        pending_count=len(pending),
        uploaded_count=len(uploaded),
        failed_count=len(failed),
    )


def list_jobs(
    channels: list[ChannelConfig],
    *,
    base: Path,
    location: Literal["queue", "uploaded", "all"] = "all",
    status: str | None = None,
) -> list[JobView]:
    """List jobs across channels (used by API /v1/jobs and CLI)."""
    jobs: list[JobView] = []
    for channel in channels:
        bundle = load_channel_jobs(channel, base=base)
        if location == "queue":
            candidates = bundle.queue_jobs
        elif location == "uploaded":
            candidates = bundle.uploaded_jobs
        else:
            candidates = bundle.queue_jobs + bundle.uploaded_jobs

        if status:
            candidates = [j for j in candidates if j.status == status]
        jobs.extend(candidates)
    return jobs
