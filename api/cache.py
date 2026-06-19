"""In-memory caches for config, token status, and dashboard snapshots."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from uploader.cache_signals import bump as bump_cache, generation
from uploader.channels import AppConfig, load_config
from uploader.oauth import OAuthSettings, resolve_oauth_settings
from uploader.oauth_web import inspect_token_file
from uploader.registry import UploadRegistry
from uploader.state_store import config_storage_uri, remote_storage_enabled

from api.schemas import ChannelOut, DashboardResponse, JobOut, TokenStatus

_lock = threading.Lock()
_config_cache: _Entry | None = None
_token_cache: dict[str, _Entry] = {}
_dashboard_cache: _Entry | None = None


@dataclass
class _Entry:
    value: Any
    stored_at: float
    config_gen: int
    queue_gen: int
    tokens_gen: int


def _ttl(name: str, default: float) -> float:
    env_key = {
        "dashboard": "UPLOADER_DASHBOARD_CACHE_TTL",
        "config": "UPLOADER_CONFIG_CACHE_TTL",
        "tokens": "UPLOADER_TOKEN_CACHE_TTL",
    }[name]
    raw = os.environ.get(env_key, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return default


def _dashboard_ttl() -> float:
    return _ttl("dashboard", 60.0)


def _config_ttl() -> float:
    return _ttl("config", 120.0)


def _token_ttl() -> float:
    return _ttl("tokens", 300.0)


def _entry_fresh(entry: _Entry | None, ttl: float, *, kinds: tuple[str, ...]) -> bool:
    if entry is None:
        return False
    if time.monotonic() - entry.stored_at > ttl:
        return False
    for kind in kinds:
        gen = generation(kind)
        if kind == "config" and entry.config_gen != gen:
            return False
        if kind == "queue" and entry.queue_gen != gen:
            return False
        if kind == "tokens" and entry.tokens_gen != gen:
            return False
    return True


def _snapshot_gens() -> tuple[int, int, int]:
    return generation("config"), generation("queue"), generation("tokens")


def invalidate_dashboard() -> None:
    with _lock:
        global _dashboard_cache
        _dashboard_cache = None


def get_cached_config(config_path) -> AppConfig:
    global _config_cache
    path = config_path.expanduser().resolve()
    with _lock:
        if _entry_fresh(_config_cache, _config_ttl(), kinds=("config",)):
            return _config_cache.value
    config = load_config(path)
    cfg_gen, _, _ = _snapshot_gens()
    with _lock:
        _config_cache = _Entry(
            value=config,
            stored_at=time.monotonic(),
            config_gen=cfg_gen,
            queue_gen=generation("queue"),
            tokens_gen=generation("tokens"),
        )
    return config


def get_token_status(
    channel_id: str,
    token_path: str,
    oauth: OAuthSettings,
) -> TokenStatus:
    with _lock:
        entry = _token_cache.get(channel_id)
        if _entry_fresh(entry, _token_ttl(), kinds=("tokens",)):
            return entry.value

    info = inspect_token_file(
        token_path,
        client_secret=oauth.client_secret_path,
        client_config=oauth.client_config,
    )
    status = TokenStatus(
        has_token=info.get("has_token", False),
        valid=info.get("valid", False),
        status=info.get("status", "unknown"),
    )
    _, _, tok_gen = _snapshot_gens()
    with _lock:
        _token_cache[channel_id] = _Entry(
            value=status,
            stored_at=time.monotonic(),
            config_gen=generation("config"),
            queue_gen=generation("queue"),
            tokens_gen=tok_gen,
        )
    return status


def _entry_to_job(entry) -> JobOut:
    return JobOut(
        id=entry.id,
        channel_id=entry.channel_id,
        status=entry.status,
        title=entry.title,
        description=entry.description,
        video_uri=entry.resolved_video_uri(),
        thumbnail_uri=entry.resolved_thumbnail_uri(),
        youtube_id=entry.youtube_id,
        youtube_url=entry.youtube_url,
        publish_at=entry.publish_at,
        created_at=entry.created_at,
        error=entry.error,
    )


def _load_channel_bundle(ch, oauth: OAuthSettings) -> tuple[ChannelOut, list[JobOut]]:
    reg = UploadRegistry(ch.registry_path)
    pending = reg.pending(channel_id=ch.id)
    auth = get_token_status(ch.id, ch.token_path, oauth)
    channel_out = ChannelOut(
        id=ch.id,
        name=ch.name,
        youtube_channel_id=ch.youtube_channel_id,
        custom_url=ch.custom_url,
        token_path=ch.token_path,
        registry_path=ch.registry_path,
        auth=auth,
        pending_count=len(pending),
    )
    jobs = [_entry_to_job(e) for e in pending]
    return channel_out, jobs


def build_dashboard(config_path, *, force: bool = False) -> DashboardResponse:
    global _dashboard_cache
    cfg_gen, queue_gen, tok_gen = _snapshot_gens()

    if not force:
        with _lock:
            if _entry_fresh(_dashboard_cache, _dashboard_ttl(), kinds=("config", "queue", "tokens")):
                return _dashboard_cache.value.model_copy(update={"cached": True})

    config = get_cached_config(config_path)
    oauth = resolve_oauth_settings(
        config.google.client_secret_path,
        oauth_port=config.google.oauth_port,
    )
    base = config_path.expanduser().resolve()
    base = base.parent.parent if base.parent.name == "config" else base.parent

    channels: list[ChannelOut] = []
    jobs: list[JobOut] = []
    if config.channels:
        with ThreadPoolExecutor(max_workers=min(8, len(config.channels))) as pool:
            futures = {
                pool.submit(_load_channel_bundle, ch, oauth): ch.id for ch in config.channels
            }
            by_id: dict[str, tuple[ChannelOut, list[JobOut]]] = {}
            for fut in as_completed(futures):
                ch_id = futures[fut]
                by_id[ch_id] = fut.result()
        for ch in config.channels:
            channel_out, ch_jobs = by_id[ch.id]
            channels.append(channel_out)
            jobs.extend(ch_jobs)

    response = DashboardResponse(
        config_uri=config_storage_uri(base),
        storage="r2" if remote_storage_enabled() else "local",
        channels=channels,
        jobs=jobs,
        cached=False,
    )

    with _lock:
        _dashboard_cache = _Entry(
            value=response,
            stored_at=time.monotonic(),
            config_gen=cfg_gen,
            queue_gen=queue_gen,
            tokens_gen=tok_gen,
        )
    return response


def clear_all_caches() -> None:
    """Used in tests."""
    global _config_cache, _dashboard_cache
    with _lock:
        _config_cache = None
        _dashboard_cache = None
        _token_cache.clear()
    bump_cache("all")
