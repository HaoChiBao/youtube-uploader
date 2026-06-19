"""Add YouTube channels dynamically after OAuth (no manual channel-a/b setup)."""

from __future__ import annotations

import re
from pathlib import Path

from uploader.channel_info import AuthorizedChannelInfo, get_authorized_channel_info
from uploader import bucket_layout
from uploader.channels import ChannelConfig, PublishConfig
from uploader.oauth import OAuthSettings
from uploader.state_store import init_channel_storage, read_raw_config, save_token, write_raw_config
from uploader.youtube_client import get_credentials

_PENDING_TOKEN = Path("secrets/.oauth_pending/youtube_token.json")


def slugify(value: str) -> str:
    """Turn a channel title or handle into a safe config id."""
    text = value.strip().lstrip("@").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or "channel"


def derive_channel_id(info: AuthorizedChannelInfo) -> str:
    """Prefer @handle, else slugified channel title, else YouTube channel id."""
    if info.custom_url:
        return slugify(info.custom_url)
    if info.title:
        slug = slugify(info.title)
        if slug:
            return slug
    return info.youtube_channel_id


def make_unique_channel_id(
    base_id: str,
    youtube_channel_id: str,
    existing: dict[str, str],
) -> str:
    """Return base_id or add a suffix when the slug collides with another channel."""
    if base_id not in existing or existing[base_id] == youtube_channel_id:
        return base_id
    suffix = youtube_channel_id[-6:].lower()
    candidate = f"{base_id}-{suffix}"
    if candidate not in existing or existing[candidate] == youtube_channel_id:
        return candidate
    return youtube_channel_id


def _config_base(config_path: Path) -> Path:
    return config_path.parent.parent if config_path.parent.name == "config" else config_path.parent


def ensure_config_file(config_path: Path) -> None:
    """Create an empty channels.yaml if it does not exist."""
    read_raw_config(config_path.expanduser().resolve())


def _read_raw_config(config_path: Path) -> dict:
    return read_raw_config(config_path, sync=True, migrate=True)


def _write_raw_config(config_path: Path, data: dict) -> None:
    write_raw_config(config_path, data)


def _channel_entry_dict(
    *,
    channel_id: str,
    name: str,
    youtube_channel_id: str,
    base: Path,
    custom_url: str = "",
    publish: PublishConfig | None = None,
) -> dict:
    pub = publish or PublishConfig()
    entry: dict = {
        "id": channel_id,
        "name": name,
        "youtube_channel_id": youtube_channel_id,
        "token_path": bucket_layout.token_location(channel_id, base),
        "registry_path": bucket_layout.registry_location(channel_id, base),
        "category_id": "10",
        "default_tags": [],
        "made_for_kids": False,
        "publish": {
            "timezone": pub.timezone,
            "hour": pub.hour,
            "interval_hours": pub.interval_hours,
        },
    }
    if custom_url:
        entry["custom_url"] = custom_url
    return entry


def _existing_youtube_ids(data: dict) -> dict[str, str]:
    """Map config channel id -> youtube_channel_id."""
    mapping: dict[str, str] = {}
    for raw in data.get("channels") or []:
        cid = raw.get("id", "")
        yt_id = raw.get("youtube_channel_id", "")
        if cid and yt_id:
            mapping[cid] = yt_id
    return mapping


def find_channel_index(data: dict, youtube_channel_id: str) -> int | None:
    for i, raw in enumerate(data.get("channels") or []):
        if raw.get("youtube_channel_id") == youtube_channel_id:
            return i
    return None


def add_and_authenticate_channel(
    oauth: OAuthSettings,
    *,
    config_path: Path | None = None,
    force_reauth: bool = True,
    publish: PublishConfig | None = None,
) -> ChannelConfig:
    """OAuth in browser, resolve YouTube channel identity, save token + channels.yaml entry."""
    if config_path is None:
        config_path = Path("config/channels.yaml")
    config_path = config_path.expanduser().resolve()
    base = _config_base(config_path)

    data = _read_raw_config(config_path)
    _PENDING_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    if _PENDING_TOKEN.is_file():
        _PENDING_TOKEN.unlink()

    creds = get_credentials(
        _PENDING_TOKEN,
        client_secret=oauth.client_secret_path,
        client_config=oauth.client_config,
        oauth_port=oauth.oauth_port,
        force_reauth=force_reauth,
    )

    info = get_authorized_channel_info(
        _PENDING_TOKEN,
        client_secret=oauth.client_secret_path,
        client_config=oauth.client_config,
        oauth_port=oauth.oauth_port,
        creds=creds,
    )

    base_id = derive_channel_id(info)
    existing = _existing_youtube_ids(data)
    channel_id = make_unique_channel_id(base_id, info.youtube_channel_id, existing)

    token_loc = save_token(
        channel_id,
        _PENDING_TOKEN.read_text(encoding="utf-8"),
        base=base,
    )
    _PENDING_TOKEN.unlink(missing_ok=True)

    init_channel_storage(
        channel_id,
        base=base,
        name=info.title,
        youtube_channel_id=info.youtube_channel_id,
        custom_url=info.custom_url,
    )

    entry = _channel_entry_dict(
        channel_id=channel_id,
        name=info.title,
        youtube_channel_id=info.youtube_channel_id,
        base=base,
        custom_url=info.custom_url,
        publish=publish,
    )

    idx = find_channel_index(data, info.youtube_channel_id)
    if idx is not None:
        data["channels"][idx] = entry
    else:
        data["channels"].append(entry)

    _write_raw_config(config_path, data)

    return ChannelConfig(
        id=channel_id,
        name=info.title,
        token_path=token_loc,
        registry_path=entry["registry_path"],
        youtube_channel_id=info.youtube_channel_id,
        custom_url=info.custom_url,
        publish=publish or PublishConfig(),
    )


def reauthenticate_channel(
    channel: ChannelConfig,
    oauth: OAuthSettings,
    *,
    config_path: Path,
) -> ChannelConfig:
    """Re-run OAuth for an existing config entry and refresh saved metadata."""
    config_path = config_path.expanduser().resolve()
    data = _read_raw_config(config_path)

    get_credentials(
        channel.token_path,
        client_secret=oauth.client_secret_path,
        client_config=oauth.client_config,
        oauth_port=oauth.oauth_port,
        force_reauth=True,
    )

    info = get_authorized_channel_info(
        channel.token_path,
        client_secret=oauth.client_secret_path,
        client_config=oauth.client_config,
        oauth_port=oauth.oauth_port,
    )

    idx = find_channel_index(data, info.youtube_channel_id)
    if idx is None:
        idx = next(
            (i for i, raw in enumerate(data.get("channels") or []) if raw.get("id") == channel.id),
            None,
        )

    if idx is not None:
        entry = data["channels"][idx]
        entry["name"] = info.title
        entry["youtube_channel_id"] = info.youtube_channel_id
        if info.custom_url:
            entry["custom_url"] = info.custom_url
        _write_raw_config(config_path, data)

    base = _config_base(config_path)
    init_channel_storage(
        channel.id,
        base=base,
        name=info.title,
        youtube_channel_id=info.youtube_channel_id,
        custom_url=info.custom_url or channel.custom_url,
    )

    return ChannelConfig(
        id=channel.id,
        name=info.title,
        token_path=channel.token_path,
        registry_path=channel.registry_path,
        youtube_channel_id=info.youtube_channel_id,
        custom_url=info.custom_url,
        category_id=channel.category_id,
        default_tags=channel.default_tags,
        made_for_kids=channel.made_for_kids,
        publish=channel.publish,
    )
