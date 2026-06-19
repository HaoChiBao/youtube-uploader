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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("channels: []\n\ngoogle:\n  oauth_port: 8080\n", encoding="utf-8")

    base = path.parent.parent if path.parent.name == "config" else path.parent
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

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
                custom_url=raw.get("custom_url", ""),
            )
        )

    if not channels:
        # Empty config is valid until the user runs `uploader channel add`.
        pass

    return AppConfig(channels=channels, google=google)


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
