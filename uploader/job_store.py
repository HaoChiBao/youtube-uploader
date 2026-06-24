"""Stage video jobs into channel storage and archive after YouTube upload."""

from __future__ import annotations

import json
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
    assert_object_readable,
    copy_object,
    delete_prefix,
    is_s3_uri,
    list_keys,
    move_prefix,
    parse_s3_uri,
    assembly_storage_bucket,
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

    meta = _build_job_metadata(
        channel=channel,
        job_id=job_id,
        title=title,
        description=description,
        config_defaults=config_defaults,
        privacy=privacy,
        is_short=is_short,
        category_id=category_id,
        tags=tags,
        made_for_kids=made_for_kids,
        language=language,
        metadata=metadata,
    )
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


def _infer_job_id_from_video_uri(video_uri: str, channel_id: str) -> str | None:
    norm = video_uri.replace("\\", "/")
    marker = f"/{bucket_layout.QUEUE_PREFIX}/{channel_id}/"
    idx = norm.find(marker)
    if idx != -1:
        rest = norm[idx + len(marker) :]
        job_part = rest.split("/", 1)[0]
        if job_part:
            return job_part

    assembly_marker = f"/music-video/{channel_id}/"
    idx = norm.find(assembly_marker)
    if idx != -1:
        rest = norm[idx + len(assembly_marker) :]
        job_part = rest.split("/", 1)[0]
        if job_part:
            return job_part
    return None


def _should_reference_uri(uri: str) -> bool:
    """Keep external-bucket URIs in place (no copy into uploader queue/)."""
    if not is_s3_uri(uri):
        return False
    bucket, _ = parse_s3_uri(uri)
    uploader_bucket = storage_bucket()
    assembly_bucket = assembly_storage_bucket()
    if assembly_bucket and bucket == assembly_bucket:
        return True
    if uploader_bucket and bucket != uploader_bucket:
        return True
    return False


def _staged_job_from_entry(
    entry: UploadEntry,
    channel: ChannelConfig,
    *,
    base: Path,
    metadata: JobMetadata | None = None,
) -> StagedJob:
    uris = bucket_layout.default_job_uris(channel.id, entry.id, base)
    meta = metadata
    if meta is None:
        from uploader.job_metadata import load_job_metadata

        meta = load_job_metadata(entry, base=base, channel=channel)
    if meta is None:
        meta = JobMetadata.for_channel(
            job_id=entry.id,
            channel=channel,
            title=entry.title,
            description=entry.description,
        )
    return StagedJob(
        channel_id=channel.id,
        job_id=entry.id,
        video_uri=entry.resolved_video_uri(),
        thumbnail_uri=entry.resolved_thumbnail_uri(),
        title_uri=uris["title_uri"],
        description_uri=uris["description_uri"],
        metadata_uri=uris["metadata_uri"],
        manifest_uri=uris["manifest_uri"],
        job_prefix=uris["job_prefix"],
        uploaded_prefix=uris["uploaded_prefix"],
        registry_path=UploadRegistry(channel.registry_path).location,
        metadata=meta,
    )


def _build_job_metadata(
    *,
    channel: ChannelConfig,
    job_id: str,
    title: str,
    description: str,
    config_defaults: JobDefaults | None,
    privacy: str | None,
    is_short: bool | None,
    category_id: str | None,
    tags: list[str] | None,
    made_for_kids: bool | None,
    language: str,
    metadata: JobMetadata | None,
) -> JobMetadata:
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
    return meta


def register_job_from_uris(
    channel: ChannelConfig,
    *,
    title: str,
    description: str,
    video_uri: str,
    thumbnail_uri: str = "",
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
) -> tuple[StagedJob, bool]:
    """Register a pending job when video files already exist in storage (R2 or local).

    When ``video_uri`` points at an external bucket (e.g. ai-music-assembler's
    ``music-assembly-data``), the job is registered by reference — metadata sidecars
    are written under ``queue/{channel_id}/{job_id}/`` but the MP4/thumbnail stay
    in place and are downloaded at upload time.

    Returns ``(staged_job, created)`` where ``created`` is False for idempotent
    re-registration of the same ``job_id``.
    """
    if not video_uri:
        raise ValueError("video_uri is required")
    assert_object_readable(video_uri)

    inferred = _infer_job_id_from_video_uri(video_uri, channel.id)
    job_id = _slugify_job_id(job_id) if job_id else (inferred or generate_job_id(channel.id))

    reg = registry or UploadRegistry(channel.registry_path)
    existing = reg.get(job_id)
    if existing is not None:
        existing_video = existing.resolved_video_uri()
        source_video = existing.extra.get("source_video_uri", existing_video)
        if video_uri in (existing_video, source_video):
            return _staged_job_from_entry(existing, channel, base=base), False
        raise ValueError(f"Job id already exists in registry with a different video_uri: {job_id}")

    uris = bucket_layout.default_job_uris(channel.id, job_id, base)
    reference_uris = _should_reference_uri(video_uri)
    if reference_uris:
        stored_video_uri = video_uri
        thumb_out = ""
        if thumbnail_uri:
            assert_object_readable(thumbnail_uri)
            thumb_out = thumbnail_uri
    else:
        canonical_video = uris["video_uri"]
        if video_uri != canonical_video:
            copy_object(video_uri, canonical_video)
        stored_video_uri = canonical_video

        thumb_out = ""
        if thumbnail_uri:
            assert_object_readable(thumbnail_uri)
            if thumbnail_uri != uris["thumbnail_uri"]:
                copy_object(thumbnail_uri, uris["thumbnail_uri"])
            thumb_out = uris["thumbnail_uri"]

    meta = _build_job_metadata(
        channel=channel,
        job_id=job_id,
        title=title,
        description=description,
        config_defaults=config_defaults,
        privacy=privacy,
        is_short=is_short,
        category_id=category_id,
        tags=tags,
        made_for_kids=made_for_kids,
        language=language,
        metadata=metadata,
    )
    write_job_metadata_files(meta, channel_id=channel.id, job_id=job_id, base=base)

    entry = UploadEntry(
        id=job_id,
        channel_id=channel.id,
        title=meta.title,
        description=meta.description,
        video_uri=stored_video_uri,
        thumbnail_uri=thumb_out,
        extra={
            "metadata_uri": uris["metadata_uri"],
            "privacy": meta.privacy,
            "is_short": meta.is_short,
            "category_id": meta.category_id,
            "tags": meta.tags,
            "made_for_kids": meta.made_for_kids,
            "source": "assembler" if reference_uris else "register",
            "reference_uris": reference_uris,
            "source_video_uri": video_uri,
        },
    )
    reg.append(entry)

    return StagedJob(
        channel_id=channel.id,
        job_id=job_id,
        video_uri=stored_video_uri,
        thumbnail_uri=thumb_out,
        title_uri=uris["title_uri"],
        description_uri=uris["description_uri"],
        metadata_uri=uris["metadata_uri"],
        manifest_uri=uris["manifest_uri"],
        job_prefix=uris["job_prefix"],
        uploaded_prefix=uris["uploaded_prefix"],
        registry_path=reg.location,
        metadata=meta,
    ), True


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

    if moved:
        _mark_metadata_uploaded(channel_id, job_id, base=base)

    return moved


def _mark_metadata_uploaded(channel_id: str, job_id: str, *, base: Path) -> None:
    """Update metadata.json status after move to uploaded/."""
    from uploader.job_metadata import JobMetadata

    meta_uri = bucket_layout.uploaded_location(channel_id, job_id, bucket_layout.JOB_METADATA, base)
    try:
        from uploader.object_storage import read_text, write_text

        text = read_text(meta_uri)
        if not text.strip():
            return
        meta = JobMetadata.from_dict(json.loads(text))
        meta.status = STATUS_UPLOADED
        write_text(meta_uri, json.dumps(meta.to_dict(), ensure_ascii=False, indent=2) + "\n")
    except Exception:
        pass


def archive_job_from_entry(
    entry: UploadEntry,
    *,
    base: Path,
    registry: UploadRegistry | None = None,
) -> list[str]:
    """Archive using channel_id + job id; update metadata and registry URIs."""
    moved = archive_job(entry.channel_id, entry.id, base=base)
    if moved and registry is not None:
        if entry.extra.get("reference_uris"):
            return moved
        from uploader.job_views import uploaded_uris_for_job

        uris = uploaded_uris_for_job(entry.channel_id, entry.id, base)
        registry.update_storage_uris(
            entry.id,
            video_uri=uris["video_uri"],
            thumbnail_uri=uris.get("thumbnail_uri", ""),
        )
    return moved


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
