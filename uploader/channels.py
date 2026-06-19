"""Load multi-channel configuration from channels.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from uploader import bucket_layout
from uploader.object_storage import is_s3_uri
from uploader.job_defaults import JobDefaults, global_job_defaults
from uploader.state_store import read_raw_config


@dataclass
class PublishConfig:
    timezone: str = "America/New_York"
    hour: int = 9
    interval_hours: float = 24.0


@dataclass
class ChannelConfig:
    id: str
    name: str = ""
    token_path: str = "youtube_token.json"
    registry_path: str = "state/channel-a/upload_registry.txt"
    category_id: str = "10"
    default_tags: list[str] = field(default_factory=list)
    made_for_kids: bool = False
    default_privacy: str = ""
    default_is_short: bool | None = None
    default_language: str = ""
    publish: PublishConfig = field(default_factory=PublishConfig)
    youtube_channel_id: str = ""
    custom_url: str = ""


@dataclass
class GoogleConfig:
    client_secret_path: Path = field(
        default_factory=lambda: Path("secrets/shared/client_secret.json")
    )
    oauth_port: int = 8080


@dataclass
class AppConfig:
    channels: list[ChannelConfig]
    google: GoogleConfig
    job_defaults: JobDefaults = field(default_factory=JobDefaults)


def _resolve_registry_path(value: str | Path, base: Path, channel_id: str) -> str:
    text = str(value).strip() if value else ""
    if not text or bucket_layout.is_default_registry_ref(text, channel_id):
        return bucket_layout.registry_location(channel_id, base)
    if is_s3_uri(text):
        return text
    return str(_resolve_path(text, base))


def _resolve_token_path(value: str | Path, base: Path, channel_id: str) -> str:
    text = str(value).strip() if value else ""
    if not text or bucket_layout.is_default_token_ref(text, channel_id):
        return bucket_layout.token_location(channel_id, base)
    if is_s3_uri(text):
        return text
    return str(_resolve_path(text, base))


def _resolve_path(value: str | Path, base: Path) -> Path:
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = (base / p).resolve()
    return p


def load_config(path: Path | None = None) -> AppConfig:
    """Load channels.yaml from path, UPLOADER_CONFIG env, or default location."""
    if path is None:
        env_path = os.environ.get("UPLOADER_CONFIG")
        if env_path:
            path = Path(env_path)
        else:
            path = Path("config/channels.yaml")

    path = path.expanduser().resolve()
    base = path.parent.parent if path.parent.name == "config" else path.parent
    data = read_raw_config(path)

    google_raw = data.get("google") or {}
    client_secret = google_raw.get("client_secret_path") or google_raw.get("client_secret")
    if os.environ.get("GOOGLE_CLIENT_SECRET_PATH"):
        client_secret = os.environ["GOOGLE_CLIENT_SECRET_PATH"]
    if not client_secret:
        client_secret = "secrets/shared/client_secret.json"

    oauth_port = int(os.environ.get("GOOGLE_OAUTH_PORT", google_raw.get("oauth_port", 8080)))
    google = GoogleConfig(
        client_secret_path=_resolve_path(client_secret, base),
        oauth_port=oauth_port,
    )

    job_defaults = JobDefaults.overlay_from_dict(
        global_job_defaults(),
        data.get("defaults"),
    )

    channels: list[ChannelConfig] = []
    for raw in data.get("channels") or []:
        channel_id = raw["id"]
        token = raw.get("token_path") or raw.get("token_secret") or ""
        registry = raw.get("registry_path") or ""
        publish_raw = raw.get("publish") or {}
        upload_raw = raw.get("upload_defaults") or raw.get("upload") or {}

        default_privacy = str(
            upload_raw.get("privacy") or raw.get("default_privacy") or ""
        ).strip()
        default_language = str(
            upload_raw.get("language") or raw.get("default_language") or ""
        ).strip()
        default_is_short: bool | None = None
        if "is_short" in upload_raw:
            default_is_short = bool(upload_raw["is_short"])
        elif "default_is_short" in raw:
            default_is_short = bool(raw["default_is_short"])

        channels.append(
            ChannelConfig(
                id=channel_id,
                name=raw.get("name", channel_id),
                token_path=_resolve_token_path(token, base, channel_id),
                registry_path=_resolve_registry_path(registry, base, channel_id),
                category_id=str(
                    upload_raw.get("category_id") or raw.get("category_id", "10")
                ),
                default_tags=list(upload_raw.get("tags") or raw.get("default_tags") or []),
                made_for_kids=bool(
                    upload_raw.get("made_for_kids")
                    if "made_for_kids" in upload_raw
                    else raw.get("made_for_kids", False)
                ),
                default_privacy=default_privacy,
                default_is_short=default_is_short,
                default_language=default_language,
                publish=PublishConfig(
                    timezone=publish_raw.get("timezone", "America/New_York"),
                    hour=int(publish_raw.get("hour", 9)),
                    interval_hours=float(publish_raw.get("interval_hours", 24)),
                ),
                youtube_channel_id=raw.get("youtube_channel_id", ""),
                custom_url=raw.get("custom_url", ""),
            )
        )

    return AppConfig(channels=channels, google=google, job_defaults=job_defaults)


def resolve_channel(config: AppConfig, ref: str) -> ChannelConfig:
    """Find a channel by config id, display name, @handle, or YouTube channel id."""
    needle = ref.strip().lower().lstrip("@")
    if not needle:
        raise KeyError("Channel reference is empty.")

    for ch in config.channels:
        if ch.id.lower() == needle:
            return ch
        if ch.name.lower() == needle:
            return ch
        if ch.youtube_channel_id.lower() == needle:
            return ch

    for ch in config.channels:
        if ch.custom_url and ch.custom_url.lower().lstrip("@") == needle:
            return ch

    ids = ", ".join(
        f"{c.id} ({c.name})" if c.name and c.name != c.id else c.id for c in config.channels
    ) or "(none — run: uploader channel add)"
    raise KeyError(f"Unknown channel {ref!r}. Configured: {ids}")


def get_channel(config: AppConfig, channel_id: str) -> ChannelConfig:
    return resolve_channel(config, channel_id)
