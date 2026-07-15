"""Add YouTube channels dynamically after OAuth (no manual channel-a/b setup)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from uploader.channel_info import AuthorizedChannelInfo, get_authorized_channel_info
from uploader.category_store import normalize_content_category, validate_channel_category
from uploader import bucket_layout
from uploader.channels import ChannelConfig, PublishConfig
from uploader.oauth import OAuthSettings
from uploader.state_store import init_channel_storage, read_raw_config, save_token, write_raw_config
from uploader.youtube_client import get_credentials

_PENDING_TOKEN = Path("secrets/.oauth_pending/youtube_token.json")

_PRESERVE_ON_MERGE = (
    "publish",
    "category",
    "category_id",
    "default_tags",
    "made_for_kids",
    "default_privacy",
    "default_is_short",
    "default_language",
    "registry_path",
)


@dataclass
class OAuthRegistrationResult:
    channel: ChannelConfig
    action: Literal["updated", "added"]


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
    category: str = "",
    long_uploads_status: str = "",
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
        "publish": _publish_dict(pub),
    }
    if custom_url:
        entry["custom_url"] = custom_url
    category = normalize_content_category(category)
    if category:
        entry["category"] = category
    if long_uploads_status:
        entry["long_uploads_status"] = long_uploads_status
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


def find_channel_index_by_id(data: dict, channel_id: str) -> int | None:
    for i, raw in enumerate(data.get("channels") or []):
        if raw.get("id") == channel_id:
            return i
    return None


def _publish_dict(pub: PublishConfig) -> dict:
    out = {
        "timezone": pub.timezone,
        "hour": pub.hour,
        "interval_hours": pub.interval_hours,
    }
    if pub.uploads_per_day is not None:
        out["uploads_per_day"] = pub.uploads_per_day
    return out


def _publish_from_entry(raw: dict) -> PublishConfig:
    pub = raw.get("publish") or {}
    uploads_raw = pub.get("uploads_per_day")
    return PublishConfig(
        timezone=pub.get("timezone", "America/New_York"),
        hour=int(pub.get("hour", 9)),
        interval_hours=float(pub.get("interval_hours", 24.0)),
        uploads_per_day=int(uploads_raw) if uploads_raw is not None else None,
    )


def _channel_config_from_entry(entry: dict) -> ChannelConfig:
    return ChannelConfig(
        id=entry["id"],
        name=entry.get("name", ""),
        token_path=entry.get("token_path", ""),
        registry_path=entry.get("registry_path", ""),
        category_id=str(entry.get("category_id", "10")),
        default_tags=list(entry.get("default_tags") or []),
        made_for_kids=bool(entry.get("made_for_kids", False)),
        default_privacy=str(entry.get("default_privacy", "")),
        default_is_short=entry.get("default_is_short"),
        default_language=str(entry.get("default_language", "")),
        publish=_publish_from_entry(entry),
        youtube_channel_id=entry.get("youtube_channel_id", ""),
        custom_url=entry.get("custom_url", ""),
        category=str(entry.get("category") or "").strip(),
        long_uploads_status=str(entry.get("long_uploads_status") or "").strip(),
    )


def _merge_preserved_settings(new_entry: dict, old_entry: dict) -> None:
    for key in _PRESERVE_ON_MERGE:
        if key in old_entry:
            new_entry[key] = old_entry[key]


def register_oauth_channel(
    creds_json: str,
    *,
    config_path: Path,
    reauth_channel_id: str | None = None,
    publish: PublishConfig | None = None,
    category: str = "",
    oauth: OAuthSettings | None = None,
    info: AuthorizedChannelInfo | None = None,
) -> OAuthRegistrationResult:
    """Save OAuth credentials and update channels.yaml.

    When ``reauth_channel_id`` is set and the signed-in YouTube channel matches the
    existing entry, refresh the token in place (id / registry / publish unchanged).

    When reauth picks a *different* YouTube account, add a new channel entry and
    leave the original channel untouched.
    """
    config_path = config_path.expanduser().resolve()
    base = _config_base(config_path)
    data = _read_raw_config(config_path)

    if category:
        validate_channel_category(category, data)

    _PENDING_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    _PENDING_TOKEN.write_text(creds_json, encoding="utf-8")
    try:
        if info is None:
            if oauth is None:
                raise ValueError("Provide oauth settings or pre-resolved channel info")
            info = get_authorized_channel_info(
                _PENDING_TOKEN,
                client_secret=oauth.client_secret_path,
                client_config=oauth.client_config,
                oauth_port=oauth.oauth_port,
            )

        if reauth_channel_id:
            idx = find_channel_index_by_id(data, reauth_channel_id)
            if idx is None:
                raise KeyError(f"Channel not found: {reauth_channel_id}")
            entry = data["channels"][idx]
            old_yt_id = (entry.get("youtube_channel_id") or "").strip()
            if old_yt_id and old_yt_id != info.youtube_channel_id:
                reauth_channel_id = None
            else:
                channel_id = reauth_channel_id
                token_loc = save_token(channel_id, creds_json, base=base)
                entry["name"] = info.title
                entry["youtube_channel_id"] = info.youtube_channel_id
                if info.custom_url:
                    entry["custom_url"] = info.custom_url
                if info.long_uploads_status:
                    entry["long_uploads_status"] = info.long_uploads_status
                elif "long_uploads_status" in entry:
                    del entry["long_uploads_status"]
                entry["token_path"] = token_loc
                if category:
                    normalized = normalize_content_category(category)
                    if normalized:
                        entry["category"] = normalized
                    elif "category" in entry:
                        del entry["category"]
                write_raw_config(config_path, data)
                init_channel_storage(
                    channel_id,
                    base=base,
                    name=info.title,
                    youtube_channel_id=info.youtube_channel_id,
                    custom_url=info.custom_url or entry.get("custom_url", ""),
                    category=entry.get("category", ""),
                )
                return OAuthRegistrationResult(
                    channel=_channel_config_from_entry(entry),
                    action="updated",
                )

        base_id = derive_channel_id(info)
        existing = _existing_youtube_ids(data)
        channel_id = make_unique_channel_id(base_id, info.youtube_channel_id, existing)
        token_loc = save_token(channel_id, creds_json, base=base)

        init_channel_storage(
            channel_id,
            base=base,
            name=info.title,
            youtube_channel_id=info.youtube_channel_id,
            custom_url=info.custom_url,
            category=category,
        )

        entry = _channel_entry_dict(
            channel_id=channel_id,
            name=info.title,
            youtube_channel_id=info.youtube_channel_id,
            base=base,
            custom_url=info.custom_url,
            publish=publish,
            category=category,
            long_uploads_status=info.long_uploads_status,
        )

        idx = find_channel_index(data, info.youtube_channel_id)
        action: Literal["updated", "added"] = "added"
        if idx is not None:
            _merge_preserved_settings(entry, data["channels"][idx])
            data["channels"][idx] = entry
            action = "updated"
        else:
            data["channels"].append(entry)

        write_raw_config(config_path, data)

        return OAuthRegistrationResult(
            channel=_channel_config_from_entry(entry),
            action=action,
        )
    finally:
        _PENDING_TOKEN.unlink(missing_ok=True)


def set_channel_category(
    channel_id: str,
    category: str,
    *,
    config_path: Path,
) -> ChannelConfig:
    """Assign or clear the assembly/content category for a channel."""
    config_path = config_path.expanduser().resolve()
    base = _config_base(config_path)
    data = _read_raw_config(config_path)
    idx = find_channel_index_by_id(data, channel_id)
    if idx is None:
        raise KeyError(f"Channel not found: {channel_id}")

    category = normalize_content_category(category)
    entry = data["channels"][idx]
    validate_channel_category(category, data)
    if category:
        entry["category"] = category
    elif "category" in entry:
        del entry["category"]

    _write_raw_config(config_path, data)

    from uploader import bucket_layout
    from uploader.object_storage import exists, read_text, write_text

    meta_loc = bucket_layout.channel_meta_location(channel_id, base)
    if exists(meta_loc):
        try:
            meta = json.loads(read_text(meta_loc))
        except json.JSONDecodeError:
            meta = {}
        if category:
            meta["category"] = category
        else:
            meta.pop("category", None)
        write_text(meta_loc, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    return _channel_config_from_entry(entry)


def patch_channel_config(
    channel_id: str,
    *,
    config_path: Path,
    category: str | None = None,
    update_category: bool = False,
    publish_timezone: str | None = None,
    publish_hour: int | None = None,
    publish_interval_hours: float | None = None,
    publish_uploads_per_day: int | None = None,
    update_publish_timezone: bool = False,
    update_publish_hour: bool = False,
    update_publish_interval_hours: bool = False,
    update_publish_uploads_per_day: bool = False,
) -> ChannelConfig:
    """Update category and/or publish scheduling for a channel."""
    config_path = config_path.expanduser().resolve()
    data = _read_raw_config(config_path)
    idx = find_channel_index_by_id(data, channel_id)
    if idx is None:
        raise KeyError(f"Channel not found: {channel_id}")

    entry = data["channels"][idx]

    if update_category:
        category = normalize_content_category(category or "")
        validate_channel_category(category, data)
        if category:
            entry["category"] = category
        elif "category" in entry:
            del entry["category"]

        base = _config_base(config_path)
        from uploader import bucket_layout
        from uploader.object_storage import exists, read_text, write_text

        meta_loc = bucket_layout.channel_meta_location(channel_id, base)
        if exists(meta_loc):
            try:
                meta = json.loads(read_text(meta_loc))
            except json.JSONDecodeError:
                meta = {}
            if category:
                meta["category"] = category
            else:
                meta.pop("category", None)
            write_text(meta_loc, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    if any(
        (
            update_publish_timezone,
            update_publish_hour,
            update_publish_interval_hours,
            update_publish_uploads_per_day,
        )
    ):
        pub = dict(entry.get("publish") or {})
        if update_publish_timezone and publish_timezone:
            pub["timezone"] = publish_timezone.strip()
        if update_publish_hour and publish_hour is not None:
            pub["hour"] = int(publish_hour)
        if update_publish_interval_hours and publish_interval_hours is not None:
            pub["interval_hours"] = float(publish_interval_hours)
        if update_publish_uploads_per_day:
            if publish_uploads_per_day is None:
                pub.pop("uploads_per_day", None)
            else:
                pub["uploads_per_day"] = int(publish_uploads_per_day)
        entry["publish"] = pub

    _write_raw_config(config_path, data)
    return _channel_config_from_entry(entry)


def add_and_authenticate_channel(
    oauth: OAuthSettings,
    *,
    config_path: Path | None = None,
    force_reauth: bool = True,
    publish: PublishConfig | None = None,
    category: str = "",
) -> ChannelConfig:
    """OAuth in browser, resolve YouTube channel identity, save token + channels.yaml entry."""
    if config_path is None:
        config_path = Path("config/channels.yaml")
    config_path = config_path.expanduser().resolve()

    _PENDING_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    if _PENDING_TOKEN.is_file():
        _PENDING_TOKEN.unlink()

    get_credentials(
        _PENDING_TOKEN,
        client_secret=oauth.client_secret_path,
        client_config=oauth.client_config,
        oauth_port=oauth.oauth_port,
        force_reauth=force_reauth,
    )

    result = register_oauth_channel(
        _PENDING_TOKEN.read_text(encoding="utf-8"),
        config_path=config_path,
        publish=publish,
        category=category,
        oauth=oauth,
    )
    return result.channel


@dataclass
class ChannelRemovalResult:
    channel_id: str
    name: str
    token_deleted: bool
    pending_jobs: int


def remove_channel_from_config(
    channel_id: str,
    *,
    config_path: Path,
) -> ChannelRemovalResult:
    """Remove a channel from channels.yaml and delete its OAuth token.

    Queue, uploaded, and registry data in storage are kept so jobs can be
    recovered if the channel is connected again with the same id.
    """
    from uploader.cache_signals import bump
    from uploader.object_storage import delete_object, exists
    from uploader.registry import UploadRegistry

    config_path = config_path.expanduser().resolve()
    base = _config_base(config_path)
    data = _read_raw_config(config_path)
    idx = find_channel_index_by_id(data, channel_id)
    if idx is None:
        raise KeyError(f"Channel not found: {channel_id}")

    entry = data["channels"].pop(idx)
    name = entry.get("name") or channel_id
    _write_raw_config(config_path, data)

    token_ref = entry.get("token_path") or bucket_layout.token_location(channel_id, base)
    token_deleted = False
    if exists(str(token_ref)):
        delete_object(str(token_ref))
        token_deleted = True
        bump("tokens")

    meta_loc = bucket_layout.channel_meta_location(channel_id, base)
    if exists(meta_loc):
        delete_object(meta_loc)

    registry_path = entry.get("registry_path") or bucket_layout.registry_location(channel_id, base)
    pending = 0
    try:
        pending = len(UploadRegistry(registry_path).pending(channel_id=channel_id))
    except Exception:
        pending = 0

    return ChannelRemovalResult(
        channel_id=channel_id,
        name=name,
        token_deleted=token_deleted,
        pending_jobs=pending,
    )


def reauthenticate_channel(
    channel: ChannelConfig,
    oauth: OAuthSettings,
    *,
    config_path: Path,
) -> OAuthRegistrationResult:
    """Re-run OAuth; same YouTube account updates in place, different account adds new."""
    config_path = config_path.expanduser().resolve()
    _PENDING_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    if _PENDING_TOKEN.is_file():
        _PENDING_TOKEN.unlink()

    get_credentials(
        _PENDING_TOKEN,
        client_secret=oauth.client_secret_path,
        client_config=oauth.client_config,
        oauth_port=oauth.oauth_port,
        force_reauth=True,
    )

    return register_oauth_channel(
        _PENDING_TOKEN.read_text(encoding="utf-8"),
        config_path=config_path,
        reauth_channel_id=channel.id,
        oauth=oauth,
    )
