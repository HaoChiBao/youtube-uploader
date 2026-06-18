"""Load multi-channel configuration from channels.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class PublishConfig:
    timezone: str = "America/New_York"
    hour: int = 9
    interval_hours: float = 24.0


@dataclass
class ChannelConfig:
    id: str
    name: str = ""
    token_path: Path = field(default_factory=lambda: Path("youtube_token.json"))
    registry_path: Path = field(
        default_factory=lambda: Path("state/channel-a/upload_registry.txt")
    )
    category_id: str = "10"
    default_tags: list[str] = field(default_factory=list)
    made_for_kids: bool = False
    publish: PublishConfig = field(default_factory=PublishConfig)
    youtube_channel_id: str = ""


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
    if not path.is_file():
        raise FileNotFoundError(
            f"Config not found: {path}\n"
            "Copy config/channels.yaml.example to config/channels.yaml and edit."
        )

    base = path.parent.parent if path.parent.name == "config" else path.parent
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    google_raw = data.get("google") or {}
    client_secret = google_raw.get("client_secret_path") or google_raw.get("client_secret")
    if os.environ.get("GOOGLE_CLIENT_SECRET_PATH"):
        client_secret = os.environ["GOOGLE_CLIENT_SECRET_PATH"]
    if not client_secret:
        client_secret = "secrets/shared/client_secret.json"

    google = GoogleConfig(
        client_secret_path=_resolve_path(client_secret, base),
        oauth_port=int(google_raw.get("oauth_port", 8080)),
    )

    channels: list[ChannelConfig] = []
    for raw in data.get("channels") or []:
        channel_id = raw["id"]
        token = raw.get("token_path") or raw.get("token_secret") or f"secrets/{channel_id}/youtube_token.json"
        registry = raw.get("registry_path") or f"state/{channel_id}/upload_registry.txt"
        publish_raw = raw.get("publish") or {}

        channels.append(
            ChannelConfig(
                id=channel_id,
                name=raw.get("name", channel_id),
                token_path=_resolve_path(token, base),
                registry_path=_resolve_path(registry, base),
                category_id=str(raw.get("category_id", "10")),
                default_tags=list(raw.get("default_tags") or []),
                made_for_kids=bool(raw.get("made_for_kids", False)),
                publish=PublishConfig(
                    timezone=publish_raw.get("timezone", "America/New_York"),
                    hour=int(publish_raw.get("hour", 9)),
                    interval_hours=float(publish_raw.get("interval_hours", 24)),
                ),
                youtube_channel_id=raw.get("youtube_channel_id", ""),
            )
        )

    if not channels:
        raise ValueError(f"No channels defined in {path}")

    return AppConfig(channels=channels, google=google)


def get_channel(config: AppConfig, channel_id: str) -> ChannelConfig:
    for ch in config.channels:
        if ch.id == channel_id:
            return ch
    ids = ", ".join(c.id for c in config.channels)
    raise KeyError(f"Unknown channel {channel_id!r}. Configured channels: {ids}")
