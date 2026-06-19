"""Default values for queued video job metadata."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

VALID_PRIVACY = ("private", "unlisted", "public")

# Built-in defaults when nothing else is configured
DEFAULT_PRIVACY = "private"
DEFAULT_IS_SHORT = False
DEFAULT_CATEGORY_ID = "10"
DEFAULT_MADE_FOR_KIDS = False
DEFAULT_LANGUAGE = "en"
DEFAULT_TAGS: list[str] = []


@dataclass
class JobDefaults:
    privacy: str = DEFAULT_PRIVACY
    is_short: bool = DEFAULT_IS_SHORT
    category_id: str = DEFAULT_CATEGORY_ID
    tags: list[str] = field(default_factory=list)
    made_for_kids: bool = DEFAULT_MADE_FOR_KIDS
    language: str = DEFAULT_LANGUAGE

    def validate(self) -> None:
        if self.privacy not in VALID_PRIVACY:
            raise ValueError(f"privacy must be one of {VALID_PRIVACY}, got {self.privacy!r}")

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> JobDefaults:
        """Parse channels.yaml ``defaults:`` — only keys present in *data* are applied."""
        return cls.overlay_from_dict(cls(), data)

    @classmethod
    def overlay_from_dict(cls, base: JobDefaults, data: dict[str, Any] | None) -> JobDefaults:
        if not data:
            return base
        return cls(
            privacy=str(data["privacy"]) if "privacy" in data else base.privacy,
            is_short=bool(data["is_short"]) if "is_short" in data else base.is_short,
            category_id=str(data["category_id"]) if "category_id" in data else base.category_id,
            tags=list(data["tags"]) if "tags" in data else list(base.tags),
            made_for_kids=bool(data["made_for_kids"]) if "made_for_kids" in data else base.made_for_kids,
            language=str(data["language"]) if "language" in data else base.language,
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def global_job_defaults() -> JobDefaults:
    """Defaults from env vars (UPLOADER_DEFAULT_*) with built-in fallbacks."""
    tags_raw = os.environ.get("UPLOADER_DEFAULT_TAGS", "").strip()
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else list(DEFAULT_TAGS)
    privacy = os.environ.get("UPLOADER_DEFAULT_PRIVACY", DEFAULT_PRIVACY).strip() or DEFAULT_PRIVACY
    return JobDefaults(
        privacy=privacy,
        is_short=_env_bool("UPLOADER_DEFAULT_IS_SHORT", DEFAULT_IS_SHORT),
        category_id=os.environ.get("UPLOADER_DEFAULT_CATEGORY_ID", DEFAULT_CATEGORY_ID).strip()
        or DEFAULT_CATEGORY_ID,
        tags=tags,
        made_for_kids=_env_bool("UPLOADER_DEFAULT_MADE_FOR_KIDS", DEFAULT_MADE_FOR_KIDS),
        language=os.environ.get("UPLOADER_DEFAULT_LANGUAGE", DEFAULT_LANGUAGE).strip()
        or DEFAULT_LANGUAGE,
    )


def merge_job_defaults(
    *,
    global_defaults: JobDefaults | None = None,
    config_defaults: JobDefaults | None = None,
    channel_privacy: str | None = None,
    channel_is_short: bool | None = None,
    channel_category_id: str | None = None,
    channel_tags: list[str] | None = None,
    channel_made_for_kids: bool | None = None,
    channel_language: str | None = None,
    override_privacy: str | None = None,
    override_is_short: bool | None = None,
    override_category_id: str | None = None,
    override_tags: list[str] | None = None,
    override_made_for_kids: bool | None = None,
    override_language: str | None = None,
) -> JobDefaults:
    """Merge defaults: built-in/env → channels.yaml defaults → channel → explicit overrides."""
    g = global_defaults or global_job_defaults()
    c = config_defaults or JobDefaults()
    merged = JobDefaults(
        privacy=c.privacy or g.privacy,
        is_short=c.is_short if c.is_short is not None else g.is_short,
        category_id=c.category_id or g.category_id,
        tags=list(c.tags or g.tags),
        made_for_kids=c.made_for_kids if c.made_for_kids is not None else g.made_for_kids,
        language=c.language or g.language,
    )
    if channel_privacy:
        merged.privacy = channel_privacy
    if channel_is_short is not None:
        merged.is_short = channel_is_short
    if channel_category_id:
        merged.category_id = channel_category_id
    if channel_tags is not None:
        merged.tags = list(channel_tags)
    if channel_made_for_kids is not None:
        merged.made_for_kids = channel_made_for_kids
    if channel_language:
        merged.language = channel_language
    if override_privacy is not None:
        merged.privacy = override_privacy
    if override_is_short is not None:
        merged.is_short = override_is_short
    if override_category_id is not None:
        merged.category_id = override_category_id
    if override_tags is not None:
        merged.tags = list(override_tags)
    if override_made_for_kids is not None:
        merged.made_for_kids = override_made_for_kids
    if override_language is not None:
        merged.language = override_language
    merged.validate()
    return merged


def defaults_for_channel(
    channel: Any,
    config_defaults: JobDefaults | None = None,
    *,
    override_privacy: str | None = None,
    override_is_short: bool | None = None,
    override_category_id: str | None = None,
    override_tags: list[str] | None = None,
    override_made_for_kids: bool | None = None,
    override_language: str | None = None,
) -> JobDefaults:
    """Resolve effective defaults for one channel (ChannelConfig-compatible)."""
    return merge_job_defaults(
        config_defaults=config_defaults,
        channel_privacy=getattr(channel, "default_privacy", None) or None,
        channel_is_short=getattr(channel, "default_is_short", None),
        channel_category_id=getattr(channel, "category_id", None),
        channel_tags=list(getattr(channel, "default_tags", None) or []),
        channel_made_for_kids=getattr(channel, "made_for_kids", None),
        channel_language=getattr(channel, "default_language", None) or None,
        override_privacy=override_privacy,
        override_is_short=override_is_short,
        override_category_id=override_category_id,
        override_tags=override_tags,
        override_made_for_kids=override_made_for_kids,
        override_language=override_language,
    )
