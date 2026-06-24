"""Canonical bucket and local paths for all uploader durable data.

Layout::

    {bucket}/
    ├── config/channels.yaml
    ├── secrets/{channel_id}/youtube_token.json
    ├── state/{channel_id}/
    │   ├── channel.meta.json
    │   └── upload_registry.txt          # JSON-lines job queue + history
    ├── queue/{channel_id}/{job_id}/     # pending YouTube uploads
    │   ├── video.mp4
    │   ├── thumbnail.png                # optional
    │   ├── title.txt
    │   ├── description.txt
    │   ├── metadata.json              # canonical upload settings (privacy, is_short, tags, …)
    │   ├── privacy.txt                # private | unlisted | public
    │   ├── is_short.txt               # true | false (YouTube Short)
    │   └── manifest.json              # staging summary + URIs
    ├── uploaded/{channel_id}/{job_id}/  # archived after successful upload
    │   └── (same files, moved from queue/)
    └── logs/{channel_id}/run-YYYYMMDD.log

Legacy prefixes ``videos/`` and ``archive/`` are still recognized when reading
existing registry entries or objects created before the rename.
"""

from __future__ import annotations

import os
from pathlib import Path

# Upload queue — files waiting for cron / scheduler
QUEUE_PREFIX = "queue"
# Post-upload retention — copies moved here after YouTube upload succeeds
UPLOADED_PREFIX = "uploaded"

# Pre-rename prefixes (backward compatibility)
LEGACY_QUEUE_PREFIX = "videos"
LEGACY_UPLOADED_PREFIX = "archive"

# Standard filenames inside queue/{channel_id}/{job_id}/
JOB_VIDEO = "video.mp4"
JOB_THUMBNAIL = "thumbnail.png"
JOB_DESCRIPTION = "description.txt"
JOB_TITLE = "title.txt"
JOB_METADATA = "metadata.json"
JOB_PRIVACY = "privacy.txt"
JOB_IS_SHORT = "is_short.txt"
JOB_MANIFEST = "manifest.json"

JOB_FILENAMES = (
    JOB_VIDEO,
    JOB_THUMBNAIL,
    JOB_DESCRIPTION,
    JOB_TITLE,
    JOB_METADATA,
    JOB_PRIVACY,
    JOB_IS_SHORT,
    JOB_MANIFEST,
)


def config_key() -> str:
    return "config/channels.yaml"


def token_key(channel_id: str) -> str:
    return f"secrets/{channel_id}/youtube_token.json"


def channel_meta_key(channel_id: str) -> str:
    return f"state/{channel_id}/channel.meta.json"


def registry_key(channel_id: str) -> str:
    return f"state/{channel_id}/upload_registry.txt"


def upload_lock_key(channel_id: str, job_id: str) -> str:
    return f"state/{channel_id}/locks/{job_id}.lock"


def queue_key(channel_id: str, job_id: str, filename: str) -> str:
    return f"{QUEUE_PREFIX}/{channel_id}/{job_id}/{filename}"


def queue_prefix_key(channel_id: str, job_id: str) -> str:
    return f"{QUEUE_PREFIX}/{channel_id}/{job_id}/"


def uploaded_key(channel_id: str, job_id: str, filename: str) -> str:
    return f"{UPLOADED_PREFIX}/{channel_id}/{job_id}/{filename}"


def uploaded_prefix_key(channel_id: str, job_id: str) -> str:
    return f"{UPLOADED_PREFIX}/{channel_id}/{job_id}/"


def job_key(channel_id: str, job_id: str, filename: str) -> str:
    """Canonical queue object key (alias for queue_key)."""
    return queue_key(channel_id, job_id, filename)


def job_prefix_key(channel_id: str, job_id: str) -> str:
    return queue_prefix_key(channel_id, job_id)


def archive_key(channel_id: str, job_id: str, filename: str) -> str:
    """Alias for uploaded_key (legacy name)."""
    return uploaded_key(channel_id, job_id, filename)


def log_key(channel_id: str, date_stamp: str) -> str:
    return f"logs/{channel_id}/run-{date_stamp}.log"


def legacy_queue_key(channel_id: str, job_id: str, filename: str) -> str:
    return f"{LEGACY_QUEUE_PREFIX}/{channel_id}/{job_id}/{filename}"


def legacy_queue_prefix_key(channel_id: str, job_id: str) -> str:
    return f"{LEGACY_QUEUE_PREFIX}/{channel_id}/{job_id}/"


def legacy_uploaded_prefix_key(channel_id: str, job_id: str) -> str:
    return f"{LEGACY_UPLOADED_PREFIX}/{channel_id}/{job_id}/"


def misconfigured_bucket_queue_prefix_key(channel_id: str, job_id: str) -> str | None:
    """Prefix when the bucket name was duplicated in the object key (bad endpoint URL)."""
    b = _bucket()
    if not b:
        return None
    return f"{b}/{QUEUE_PREFIX}/{channel_id}/{job_id}/"


def misconfigured_bucket_uploaded_prefix_key(channel_id: str, job_id: str) -> str | None:
    b = _bucket()
    if not b:
        return None
    return f"{b}/{UPLOADED_PREFIX}/{channel_id}/{job_id}/"


def queue_prefix_candidates(channel_id: str, job_id: str) -> tuple[str, ...]:
    """Queue prefixes to search (canonical first, then legacy / misconfigured)."""
    keys: list[str] = [
        queue_prefix_key(channel_id, job_id),
        legacy_queue_prefix_key(channel_id, job_id),
    ]
    nested = misconfigured_bucket_queue_prefix_key(channel_id, job_id)
    if nested:
        keys.append(nested)
    return tuple(keys)


def uploaded_prefix_candidates(channel_id: str, job_id: str) -> tuple[str, ...]:
    """Uploaded prefixes to search (canonical first, then legacy / misconfigured)."""
    keys: list[str] = [
        uploaded_prefix_key(channel_id, job_id),
        legacy_uploaded_prefix_key(channel_id, job_id),
    ]
    nested = misconfigured_bucket_uploaded_prefix_key(channel_id, job_id)
    if nested:
        keys.append(nested)
    return tuple(keys)


def _bucket() -> str:
    return (
        os.environ.get("CLOUDFLARE_R2_BUCKET", "").strip()
        or os.environ.get("UPLOADER_STORAGE_BUCKET", "").strip()
    )


def prefix_location(key_prefix: str, base: Path) -> str:
    """Resolve a trailing-slash prefix to s3:// or local path."""
    if _bucket():
        return s3_uri(key_prefix)
    return str(local_path(base, key_prefix.rstrip("/")).resolve()) + "/"


def s3_uri(key: str, *, bucket: str | None = None) -> str:
    b = bucket or _bucket()
    if not b:
        raise ValueError("CLOUDFLARE_R2_BUCKET is not set")
    return f"s3://{b}/{key}"


def local_path(base: Path, key: str) -> Path:
    return (base / key).resolve()


def resolve_location(key: str, base: Path) -> str:
    """Return s3:// URI when R2 is configured, else a local absolute path."""
    if _bucket():
        return s3_uri(key)
    return str(local_path(base, key))


def config_location(base: Path) -> str:
    return resolve_location(config_key(), base)


def token_location(channel_id: str, base: Path) -> str:
    return resolve_location(token_key(channel_id), base)


def channel_meta_location(channel_id: str, base: Path) -> str:
    return resolve_location(channel_meta_key(channel_id), base)


def registry_location(channel_id: str, base: Path) -> str:
    return resolve_location(registry_key(channel_id), base)


def job_location(channel_id: str, job_id: str, filename: str, base: Path) -> str:
    return resolve_location(queue_key(channel_id, job_id, filename), base)


def job_prefix_location(channel_id: str, job_id: str, base: Path) -> str:
    key = queue_prefix_key(channel_id, job_id)
    if _bucket():
        return s3_uri(key)
    return str(local_path(base, key).resolve()) + "/"


def uploaded_location(channel_id: str, job_id: str, filename: str, base: Path) -> str:
    return resolve_location(uploaded_key(channel_id, job_id, filename), base)


def uploaded_prefix_location(channel_id: str, job_id: str, base: Path) -> str:
    key = uploaded_prefix_key(channel_id, job_id)
    if _bucket():
        return s3_uri(key)
    return str(local_path(base, key).resolve()) + "/"


def archive_location(channel_id: str, job_id: str, filename: str, base: Path) -> str:
    return uploaded_location(channel_id, job_id, filename, base)


def log_location(channel_id: str, date_stamp: str, base: Path) -> str:
    return resolve_location(log_key(channel_id, date_stamp), base)


def default_job_uris(
    channel_id: str,
    job_id: str,
    base: Path,
    *,
    include_optional: bool = True,
) -> dict[str, str]:
    """Canonical URIs for a queued video job (for staging / assembler helpers)."""
    uris = {
        "video_uri": job_location(channel_id, job_id, JOB_VIDEO, base),
        "thumbnail_uri": job_location(channel_id, job_id, JOB_THUMBNAIL, base),
        "job_prefix": job_prefix_location(channel_id, job_id, base),
        "uploaded_prefix": uploaded_prefix_location(channel_id, job_id, base),
    }
    if include_optional:
        uris["description_uri"] = job_location(channel_id, job_id, JOB_DESCRIPTION, base)
        uris["title_uri"] = job_location(channel_id, job_id, JOB_TITLE, base)
        uris["metadata_uri"] = job_location(channel_id, job_id, JOB_METADATA, base)
        uris["privacy_uri"] = job_location(channel_id, job_id, JOB_PRIVACY, base)
        uris["is_short_uri"] = job_location(channel_id, job_id, JOB_IS_SHORT, base)
        uris["manifest_uri"] = job_location(channel_id, job_id, JOB_MANIFEST, base)
    return uris


def local_channel_dirs(base: Path, channel_id: str) -> list[Path]:
    """Local directories to create for one channel (mirrors bucket prefixes)."""
    return [
        local_path(base, f"secrets/{channel_id}"),
        local_path(base, f"state/{channel_id}"),
        local_path(base, f"{QUEUE_PREFIX}/{channel_id}"),
        local_path(base, f"{UPLOADED_PREFIX}/{channel_id}"),
        local_path(base, f"logs/{channel_id}"),
    ]


def is_default_token_ref(value: str, channel_id: str) -> bool:
    normalized = value.replace("\\", "/").lstrip("./")
    return normalized == token_key(channel_id) or normalized.endswith(f"/{token_key(channel_id)}")


def is_default_registry_ref(value: str, channel_id: str) -> bool:
    normalized = value.replace("\\", "/").lstrip("./")
    return normalized == registry_key(channel_id) or normalized.endswith(f"/{registry_key(channel_id)}")
