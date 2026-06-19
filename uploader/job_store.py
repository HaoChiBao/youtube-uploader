"""Stage video jobs into channel storage and archive after YouTube upload."""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from uploader import bucket_layout
from uploader.channels import ChannelConfig
from uploader.job_metadata import JobMetadata, write_job_metadata_files
from uploader.job_defaults import JobDefaults
from uploader.object_storage import (
    delete_prefix,
    is_s3_uri,
    list_keys,
    move_prefix,
    storage_bucket,
    upload_file,
)
from uploader.registry import STATUS_PENDING, STATUS_UPLOADING, UploadEntry, UploadRegistry


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify_job_id(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "-", text)
    return text.strip("-") or "job"


def generate_job_id(channel_id: str, *, suffix: str | None = None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = f"{_slugify_job_id(channel_id)}_{stamp}"
    if suffix:
        return f"{base}_{_slugify_job_id(suffix)}"
    return base


def _guess_content_type(path: Path) -> str | None:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed


@dataclass
class StagedJob:
    channel_id: str
    job_id: str
    video_uri: str
    thumbnail_uri: str
    title_uri: str
    description_uri: str
    metadata_uri: str
    manifest_uri: str
    job_prefix: str
    uploaded_prefix: str
    registry_path: str
    metadata: JobMetadata


def stage_job(
    channel: ChannelConfig,
    *,
    video_path: Path,
    title: str,
    description: str,
    thumbnail_path: Path | None = None,
    job_id: str | None = None,
    base: Path,
    registry: UploadRegistry | None = None,
    config_defaults: JobDefaults | None = None,
    privacy: str | None = None,
    is_short: bool | None = None,
    category_id: str | None = None,
    tags: list[str] | None = None,
    made_for_kids: bool | None = None,
    language: str = "",
    metadata: JobMetadata | None = None,
) -> StagedJob:
    """Upload a video job folder to the channel queue and append a pending registry row.

    Each job is stored under ``queue/{channel_id}/{job_id}/`` with:

    - ``video.mp4``, ``thumbnail.png`` (optional)
    - ``title.txt``, ``description.txt``
    - ``metadata.json`` — privacy, is_short, tags, category_id, made_for_kids, …
    - ``privacy.txt``, ``is_short.txt`` — human-readable flags
    - ``manifest.json`` — staging summary + URIs
    """
    video_path = video_path.expanduser().resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    if thumbnail_path is not None:
        thumbnail_path = thumbnail_path.expanduser().resolve()
        if not thumbnail_path.is_file():
            raise FileNotFoundError(f"Thumbnail file not found: {thumbnail_path}")

    job_id = _slugify_job_id(job_id) if job_id else generate_job_id(channel.id)
    uris = bucket_layout.default_job_uris(channel.id, job_id, base)

    reg = registry or UploadRegistry(channel.registry_path)
    if reg.get(job_id):
        raise ValueError(f"Job id already exists in registry: {job_id}")

    upload_file(video_path, uris["video_uri"], content_type=_guess_content_type(video_path))

    thumbnail_uri = ""
    if thumbnail_path is not None:
        upload_file(
            thumbnail_path,
            uris["thumbnail_uri"],
            content_type=_guess_content_type(thumbnail_path),
        )
        thumbnail_uri = uris["thumbnail_uri"]

    if metadata is not None:
        meta = metadata
        if title:
            meta.title = title
        if description:
            meta.description = description
        if privacy is not None:
            meta.privacy = privacy
        if is_short is not None:
            meta.is_short = is_short
        if category_id is not None:
            meta.category_id = category_id
        if tags is not None:
            meta.tags = tags
        if made_for_kids is not None:
            meta.made_for_kids = made_for_kids
        if language is not None:
            meta.language = language
        if job_id:
            meta.id = job_id
        if not meta.channel_id:
            meta.channel_id = channel.id
    else:
        meta = JobMetadata.for_channel(
            job_id=job_id,
            channel=channel,
            title=title,
            description=description,
            config_defaults=config_defaults,
            privacy=privacy,
            is_short=is_short,
            category_id=category_id,
            tags=tags,
            made_for_kids=made_for_kids,
            language=language,
        )
    meta.staged_at = _utc_now_iso()
    meta.status = STATUS_PENDING
    meta.validate()

    write_job_metadata_files(meta, channel_id=channel.id, job_id=job_id, base=base)

    entry = UploadEntry(
        id=job_id,
        channel_id=channel.id,
        title=meta.title,
        description=meta.description,
        video_uri=uris["video_uri"],
        thumbnail_uri=thumbnail_uri,
        extra={
            "metadata_uri": uris["metadata_uri"],
            "privacy": meta.privacy,
            "is_short": meta.is_short,
            "category_id": meta.category_id,
            "tags": meta.tags,
            "made_for_kids": meta.made_for_kids,
        },
    )
    reg.append(entry)

    return StagedJob(
        channel_id=channel.id,
        job_id=job_id,
        video_uri=uris["video_uri"],
        thumbnail_uri=thumbnail_uri,
        title_uri=uris["title_uri"],
        description_uri=uris["description_uri"],
        metadata_uri=uris["metadata_uri"],
        manifest_uri=uris["manifest_uri"],
        job_prefix=uris["job_prefix"],
        uploaded_prefix=uris["uploaded_prefix"],
        registry_path=reg.location,
        metadata=meta,
    )


def _prefix_has_objects(prefix: str) -> bool:
    prefix = prefix.rstrip("/") + "/"
    if is_s3_uri(prefix):
        return bool(list_keys(prefix))
    root = Path(prefix.rstrip("/"))
    return root.is_dir() and any(root.iterdir())


def archive_job(channel_id: str, job_id: str, *, base: Path) -> list[str]:
    """Move a job folder from queue/ to uploaded/ after successful YouTube upload."""
    moved: list[str] = []
    dest_prefix = bucket_layout.uploaded_prefix_location(channel_id, job_id, base)
    dest = dest_prefix if dest_prefix.endswith("/") else dest_prefix + "/"

    for key_prefix in bucket_layout.queue_prefix_candidates(channel_id, job_id):
        if storage_bucket():
            src = bucket_layout.s3_uri(key_prefix)
        else:
            src = str(bucket_layout.local_path(base, key_prefix.rstrip("/"))) + "/"
        if not _prefix_has_objects(src):
            continue
        moved = move_prefix(src, dest)
        break

    return moved


def archive_job_from_entry(entry: UploadEntry, *, base: Path) -> list[str]:
    """Archive using channel_id + job id from a registry entry."""
    return archive_job(entry.channel_id, entry.id, base=base)


@dataclass
class RemovedJob:
    channel_id: str
    job_id: str
    registry_path: str
    deleted_paths: list[str]


def remove_job(
    channel: ChannelConfig,
    job_id: str,
    *,
    base: Path,
    registry: UploadRegistry | None = None,
) -> RemovedJob:
    """Remove a pending/failed job from queue storage and drop its registry row."""
    job_id = _slugify_job_id(job_id)
    reg = registry or UploadRegistry(channel.registry_path)
    entry = reg.get(job_id)
    if entry is None:
        raise ValueError(f"Job not found in registry: {job_id}")
    if entry.status == STATUS_UPLOADING:
        raise ValueError(f"Job {job_id} is currently uploading; cannot remove")

    deleted: list[str] = []
    for key_prefix in bucket_layout.queue_prefix_candidates(channel.id, job_id):
        if storage_bucket():
            src = bucket_layout.s3_uri(key_prefix)
        else:
            src = str(bucket_layout.local_path(base, key_prefix.rstrip("/"))) + "/"
        if not _prefix_has_objects(src):
            continue
        deleted.extend(delete_prefix(src))

    reg.remove(job_id)
    return RemovedJob(
        channel_id=channel.id,
        job_id=job_id,
        registry_path=reg.location,
        deleted_paths=deleted,
    )
