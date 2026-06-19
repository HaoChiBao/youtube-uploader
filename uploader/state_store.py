"""Persist channels.yaml and OAuth tokens in R2 when CLOUDFLARE_R2_BUCKET is set."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from uploader import bucket_layout
from uploader.object_storage import (
    exists,
    is_s3_uri,
    read_text,
    write_text,
)

_EMPTY_CONFIG = "channels: []\n\ngoogle:\n  oauth_port: 8080\n"


def remote_storage_enabled() -> bool:
    return bool(bucket_layout._bucket())


def config_base_from_path(local_path: Path) -> Path:
    local_path = local_path.expanduser().resolve()
    if local_path.parent.name == "config":
        return local_path.parent.parent
    return local_path.parent


def _parse_yaml(text: str) -> dict:
    data = yaml.safe_load(text) or {}
    if "channels" not in data:
        data["channels"] = []
    return data


def normalize_channel_entry(raw: dict, base: Path) -> dict:
    """Rewrite token_path and registry_path to canonical bucket layout locations."""
    channel_id = raw.get("id", "")
    if not channel_id:
        return raw
    raw["token_path"] = bucket_layout.token_location(channel_id, base)
    raw["registry_path"] = bucket_layout.registry_location(channel_id, base)
    return raw


def _migrate_local_file(local: Path, remote_loc: str) -> bool:
    if exists(remote_loc):
        return False
    if not local.is_file():
        return False
    write_text(remote_loc, local.read_text(encoding="utf-8"))
    return True


def _migrate_channel_files(raw: dict, base: Path) -> bool:
    """Upload local channel files to R2 when missing remotely. Returns True if any migrated."""
    if not remote_storage_enabled():
        return False
    channel_id = raw.get("id", "")
    if not channel_id:
        return False
    migrated = False

    remote_token = bucket_layout.token_location(channel_id, base)
    if not exists(remote_token):
        token_candidates: list[Path] = []
        token_ref = raw.get("token_path") or bucket_layout.token_key(channel_id)
        if not is_s3_uri(str(token_ref)):
            local = Path(str(token_ref))
            if not local.is_absolute():
                local = (base / local).resolve()
            token_candidates.append(local)
        token_candidates.append(
            bucket_layout.local_path(base, bucket_layout.token_key(channel_id))
        )
        for local in token_candidates:
            if _migrate_local_file(local, remote_token):
                migrated = True
                break

    remote_reg = bucket_layout.registry_location(channel_id, base)
    if not exists(remote_reg):
        reg_candidates: list[Path] = []
        reg_ref = raw.get("registry_path") or bucket_layout.registry_key(channel_id)
        if not is_s3_uri(str(reg_ref)):
            local = Path(str(reg_ref))
            if not local.is_absolute():
                local = (base / local).resolve()
            reg_candidates.append(local)
        reg_candidates.append(
            bucket_layout.local_path(base, bucket_layout.registry_key(channel_id))
        )
        for local in reg_candidates:
            if _migrate_local_file(local, remote_reg):
                migrated = True
                break

    meta_local = bucket_layout.local_path(base, bucket_layout.channel_meta_key(channel_id))
    if _migrate_local_file(meta_local, bucket_layout.channel_meta_location(channel_id, base)):
        migrated = True

    return migrated


def migrate_config_data(data: dict, base: Path) -> bool:
    """Normalize paths and migrate local files to R2. Returns True if config changed."""
    changed = False
    for i, raw in enumerate(data.get("channels") or []):
        before_token = raw.get("token_path")
        before_reg = raw.get("registry_path")
        normalize_channel_entry(raw, base)
        if raw.get("token_path") != before_token or raw.get("registry_path") != before_reg:
            changed = True
        if _migrate_channel_files(raw, base):
            changed = True
        data["channels"][i] = raw
    return changed


def read_raw_config(local_path: Path) -> dict:
    """Load channels.yaml from R2 (primary) or local disk, migrating local → R2 once."""
    local_path = local_path.expanduser().resolve()
    base = config_base_from_path(local_path)

    if remote_storage_enabled():
        loc = bucket_layout.config_location(base)
        text = read_text(loc)
        if not text.strip():
            if local_path.is_file():
                text = local_path.read_text(encoding="utf-8")
                write_text(loc, text)
            else:
                text = _EMPTY_CONFIG
                write_text(loc, text)
        data = _parse_yaml(text)
        if migrate_config_data(data, base):
            write_raw_config(local_path, data)
        return data

    local_path.parent.mkdir(parents=True, exist_ok=True)
    if not local_path.is_file():
        local_path.write_text(_EMPTY_CONFIG, encoding="utf-8")
    return _parse_yaml(local_path.read_text(encoding="utf-8"))


def write_raw_config(local_path: Path, data: dict) -> None:
    """Save channels.yaml to R2 (primary) and mirror to local."""
    local_path = local_path.expanduser().resolve()
    base = config_base_from_path(local_path)
    for i, raw in enumerate(data.get("channels") or []):
        data["channels"][i] = normalize_channel_entry(dict(raw), base)
    body = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    if remote_storage_enabled():
        write_text(bucket_layout.config_location(base), body)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(body, encoding="utf-8")


def save_token(channel_id: str, token_json: str, *, base: Path) -> str:
    loc = bucket_layout.token_location(channel_id, base)
    write_text(loc, token_json)
    return loc


def token_is_authorized(token_ref: str | Path) -> bool:
    return exists(str(token_ref))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init_channel_storage(
    channel_id: str,
    *,
    base: Path,
    name: str,
    youtube_channel_id: str,
    custom_url: str = "",
) -> list[str]:
    """Create per-channel bucket structure. Returns list of paths created."""
    created: list[str] = []
    meta = {
        "id": channel_id,
        "name": name,
        "youtube_channel_id": youtube_channel_id,
        "custom_url": custom_url,
        "authenticated_at": _utc_now_iso(),
    }
    meta_json = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"

    meta_loc = bucket_layout.channel_meta_location(channel_id, base)
    write_text(meta_loc, meta_json)
    created.append(meta_loc)

    reg_loc = bucket_layout.registry_location(channel_id, base)
    if not exists(reg_loc):
        write_text(reg_loc, "")
        created.append(reg_loc)

    for directory in bucket_layout.local_channel_dirs(base, channel_id):
        directory.mkdir(parents=True, exist_ok=True)

    return created


def ensure_bucket_structure(local_config_path: Path) -> list[str]:
    """Initialize config and all configured channels in the bucket layout."""
    local_config_path = local_config_path.expanduser().resolve()
    base = config_base_from_path(local_config_path)
    created: list[str] = []

    data = read_raw_config(local_config_path)

    config_loc = bucket_layout.config_location(base)
    if not exists(config_loc):
        write_text(config_loc, _EMPTY_CONFIG if not data.get("channels") else yaml.safe_dump(data, sort_keys=False))
        created.append(config_loc)

    for raw in data.get("channels") or []:
        channel_id = raw.get("id", "")
        if not channel_id:
            continue
        name = raw.get("name", channel_id)
        yt_id = raw.get("youtube_channel_id", "")
        custom_url = raw.get("custom_url", "")
        created.extend(
            init_channel_storage(
                channel_id,
                base=base,
                name=name,
                youtube_channel_id=yt_id,
                custom_url=custom_url,
            )
        )

    if migrate_config_data(data, base):
        write_raw_config(local_config_path, data)

    return created
