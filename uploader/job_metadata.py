"""Per-job upload metadata stored alongside queue folder assets."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from uploader import bucket_layout
from uploader.channels import ChannelConfig
from uploader.object_storage import is_s3_uri, parse_s3_uri, read_text, write_text
from uploader.registry import UploadEntry

from uploader.job_defaults import (
    DEFAULT_CATEGORY_ID,
    DEFAULT_IS_SHORT,
    DEFAULT_LANGUAGE,
    DEFAULT_MADE_FOR_KIDS,
    DEFAULT_PRIVACY,
    JobDefaults,
    VALID_PRIVACY,
    defaults_for_channel,
)


@dataclass
class JobMetadata:
    """All YouTube upload fields for one queued job folder."""

    id: str
    channel_id: str
    title: str
    description: str
    privacy: str = DEFAULT_PRIVACY
    is_short: bool = DEFAULT_IS_SHORT
    category_id: str = DEFAULT_CATEGORY_ID
    tags: list[str] = field(default_factory=list)
    made_for_kids: bool = DEFAULT_MADE_FOR_KIDS
    language: str = DEFAULT_LANGUAGE
    video_file: str = bucket_layout.JOB_VIDEO
    thumbnail_file: str = bucket_layout.JOB_THUMBNAIL
    staged_at: str = ""
    status: str = "pending"

    def validate(self) -> None:
        if self.privacy not in VALID_PRIVACY:
            raise ValueError(f"privacy must be one of {VALID_PRIVACY}, got {self.privacy!r}")

    def effective_tags(self) -> list[str]:
        tags = list(self.tags)
        if self.is_short and not any(t.lower() == "shorts" for t in tags):
            tags.append("Shorts")
        return tags

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobMetadata:
        fields = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        kwargs = {k: data[k] for k in fields if k in data}
        if "tags" in kwargs and kwargs["tags"] is None:
            kwargs["tags"] = []
        return cls(**kwargs)

    @classmethod
    def for_channel(
        cls,
        *,
        job_id: str,
        channel: ChannelConfig,
        title: str,
        description: str,
        config_defaults: JobDefaults | None = None,
        privacy: str | None = None,
        is_short: bool | None = None,
        category_id: str | None = None,
        tags: list[str] | None = None,
        made_for_kids: bool | None = None,
        language: str | None = None,
    ) -> JobMetadata:
        from uploader.job_defaults import defaults_for_channel

        defaults = defaults_for_channel(
            channel,
            config_defaults,
            override_privacy=privacy,
            override_is_short=is_short,
            override_category_id=category_id,
            override_tags=tags,
            override_made_for_kids=made_for_kids,
            override_language=language,
        )
        return cls(
            id=job_id,
            channel_id=channel.id,
            title=title,
            description=description,
            privacy=defaults.privacy,
            is_short=defaults.is_short,
            category_id=defaults.category_id,
            tags=list(defaults.tags),
            made_for_kids=defaults.made_for_kids,
            language=defaults.language,
        )


def _parse_bool(text: str) -> bool:
    return text.strip().lower() in ("1", "true", "yes", "y", "on")


def _job_dir_from_video_uri(video_uri: str, base: Path) -> Path | None:
    if not video_uri:
        return None
    if is_s3_uri(video_uri):
        from uploader.object_storage import parse_s3_uri

        _, key = parse_s3_uri(video_uri)
        parts = key.split("/")
        if len(parts) < 2:
            return None
        job_dir_key = "/".join(parts[:-1])
        return bucket_layout.local_path(base, job_dir_key)
    path = Path(video_uri)
    if path.is_file():
        return path.parent
    if path.is_dir():
        return path
    return path.parent if path.parent.exists() else None


def metadata_uri_for_video_uri(video_uri: str, base: Path) -> str:
    """Resolve metadata.json URI/path next to video_uri."""
    if is_s3_uri(video_uri):
        from uploader.object_storage import parse_s3_uri

        bucket, key = parse_s3_uri(video_uri)
        parts = key.split("/")
        if len(parts) < 2:
            raise ValueError(f"Cannot derive job folder from video_uri: {video_uri}")
        meta_key = "/".join(parts[:-1] + [bucket_layout.JOB_METADATA])
        return f"s3://{bucket}/{meta_key}"
    path = Path(video_uri)
    job_dir = path.parent if path.is_file() else path
    return str((job_dir / bucket_layout.JOB_METADATA).resolve())


def write_job_metadata_files(
    meta: JobMetadata,
    *,
    channel_id: str,
    job_id: str,
    base: Path,
) -> dict[str, str]:
    """Write metadata.json plus human-readable sidecar files in the job folder."""
    meta.validate()
    uris = bucket_layout.default_job_uris(channel_id, job_id, base, include_optional=True)

    write_text(uris["title_uri"], meta.title.rstrip() + "\n")
    write_text(uris["description_uri"], meta.description.rstrip() + "\n")
    write_text(uris["metadata_uri"], json.dumps(meta.to_dict(), ensure_ascii=False, indent=2) + "\n")
    write_text(uris["privacy_uri"], meta.privacy + "\n")
    write_text(uris["is_short_uri"], ("true" if meta.is_short else "false") + "\n")

    # manifest.json — staging summary (includes URIs for upstream tools)
    manifest = {
        **meta.to_dict(),
        "video_uri": uris["video_uri"],
        "thumbnail_uri": uris.get("thumbnail_uri", ""),
        "metadata_uri": uris["metadata_uri"],
    }
    write_text(uris["manifest_uri"], json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    return uris


def load_job_metadata(
    entry: UploadEntry,
    *,
    base: Path,
    channel: ChannelConfig | None = None,
    config_defaults: JobDefaults | None = None,
) -> JobMetadata | None:
    """Load metadata from the job folder; fall back to registry + channel defaults."""
    video_uri = entry.resolved_video_uri()
    if not video_uri:
        return None

    meta_uri = entry.extra.get("metadata_uri") if entry.extra else None
    if not meta_uri:
        try:
            meta_uri = metadata_uri_for_video_uri(video_uri, base)
        except ValueError:
            meta_uri = None

    data: dict[str, Any] = {}
    if meta_uri:
        text = read_text(meta_uri)
        if text.strip():
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {}

    if not data.get("title") and entry.title:
        data["title"] = entry.title
    if not data.get("description") and entry.description:
        data["description"] = entry.description
    if not data.get("id"):
        data["id"] = entry.id
    if not data.get("channel_id"):
        data["channel_id"] = entry.channel_id

    # Sidecar fallbacks
    if meta_uri:
        job_dir_uri = meta_uri.rsplit("/", 1)[0] if is_s3_uri(meta_uri) else str(Path(meta_uri).parent)
        prefix = job_dir_uri if is_s3_uri(job_dir_uri) else str(Path(job_dir_uri))
        if not is_s3_uri(prefix):
            sidecar_base = Path(prefix)
        else:
            sidecar_base = None

        if sidecar_base is not None:
            _merge_sidecar(sidecar_base, data)
        else:
            _merge_sidecar_uris(prefix, data)

    if not data:
        if channel is None:
            return None
        return JobMetadata.for_channel(
            job_id=entry.id,
            channel=channel,
            title=entry.title,
            description=entry.description,
            config_defaults=config_defaults,
        )

    meta = JobMetadata.from_dict(data)
    if channel is not None:
        effective = defaults_for_channel(channel, config_defaults)
        if "privacy" not in data:
            meta.privacy = effective.privacy
        if "is_short" not in data:
            meta.is_short = effective.is_short
        if "category_id" not in data:
            meta.category_id = effective.category_id
        if "tags" not in data:
            meta.tags = list(effective.tags)
        if "made_for_kids" not in data:
            meta.made_for_kids = effective.made_for_kids
        if "language" not in data:
            meta.language = effective.language
    meta.validate()
    return meta


def _merge_sidecar(job_dir: Path, data: dict[str, Any]) -> None:
    title_path = job_dir / bucket_layout.JOB_TITLE
    if not data.get("title") and title_path.is_file():
        data["title"] = title_path.read_text(encoding="utf-8").strip()
    desc_path = job_dir / bucket_layout.JOB_DESCRIPTION
    if not data.get("description") and desc_path.is_file():
        data["description"] = desc_path.read_text(encoding="utf-8").strip()
    privacy_path = job_dir / bucket_layout.JOB_PRIVACY
    if not data.get("privacy") and privacy_path.is_file():
        data["privacy"] = privacy_path.read_text(encoding="utf-8").strip()
    short_path = job_dir / bucket_layout.JOB_IS_SHORT
    if "is_short" not in data and short_path.is_file():
        data["is_short"] = _parse_bool(short_path.read_text(encoding="utf-8"))


def _merge_sidecar_uris(job_prefix: str, data: dict[str, Any]) -> None:
    prefix = job_prefix.rstrip("/") + "/"

    def _read_sidecar(filename: str) -> str:
        if is_s3_uri(prefix):
            bucket, key_prefix = parse_s3_uri(prefix)
            return read_text(f"s3://{bucket}/{key_prefix}{filename}")
        return (Path(prefix) / filename).read_text(encoding="utf-8") if (Path(prefix) / filename).is_file() else ""

    if not data.get("title"):
        text = _read_sidecar(bucket_layout.JOB_TITLE)
        if text.strip():
            data["title"] = text.strip()
    if not data.get("description"):
        text = _read_sidecar(bucket_layout.JOB_DESCRIPTION)
        if text.strip():
            data["description"] = text.strip()
    if not data.get("privacy"):
        text = _read_sidecar(bucket_layout.JOB_PRIVACY)
        if text.strip():
            data["privacy"] = text.strip()
    if "is_short" not in data:
        text = _read_sidecar(bucket_layout.JOB_IS_SHORT)
        if text.strip():
            data["is_short"] = _parse_bool(text)
