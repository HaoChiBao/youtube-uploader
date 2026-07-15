"""In-memory caches for config, token status, and dashboard snapshots."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uploader.cache_signals import bump as bump_cache, generation
from uploader.channels import AppConfig, load_config
from uploader.job_views import JobView, load_channel_jobs
from uploader.oauth import OAuthSettings, resolve_oauth_settings
from uploader.oauth_web import inspect_token_file
from uploader.state_store import config_storage_uri, remote_storage_enabled

from api.schemas import ChannelOut, DashboardResponse, JobOut, PublishConfigOut, TokenStatus
from uploader.channel_info import get_authorized_channel_info, is_channel_verified

_lock = threading.Lock()
_config_cache: _Entry | None = None
_token_cache: dict[str, _Entry] = {}
_verification_cache: dict[str, _Entry] = {}
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
        "verification": "UPLOADER_VERIFICATION_CACHE_TTL",
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


def _verification_ttl() -> float:
    return _ttl("verification", 3600.0)


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


def resolve_long_uploads_status(
    channel_id: str,
    token_path: str,
    oauth: OAuthSettings,
    *,
    auth_valid: bool,
    stored_status: str = "",
) -> str:
    """Return longUploadsStatus, refreshing from YouTube when the token is valid."""
    stored = (stored_status or "").strip()
    if not auth_valid:
        return stored

    with _lock:
        entry = _verification_cache.get(channel_id)
        if _entry_fresh(entry, _verification_ttl(), kinds=("tokens",)):
            return entry.value or stored

    status = stored
    try:
        info = get_authorized_channel_info(
            token_path,
            client_secret=oauth.client_secret_path,
            client_config=oauth.client_config,
            oauth_port=oauth.oauth_port,
        )
        if info.long_uploads_status:
            status = info.long_uploads_status
    except Exception:
        pass

    _, _, tok_gen = _snapshot_gens()
    with _lock:
        _verification_cache[channel_id] = _Entry(
            value=status,
            stored_at=time.monotonic(),
            config_gen=generation("config"),
            queue_gen=generation("queue"),
            tokens_gen=tok_gen,
        )
    return status


def job_view_to_out(view: JobView) -> JobOut:
    return JobOut(**view.to_dict())


def _load_channel_bundle(
    ch, oauth: OAuthSettings, *, base: Path
) -> tuple[ChannelOut, list[JobOut], list[JobOut], list[JobOut]]:
    bundle = load_channel_jobs(ch, base=base)
    auth = get_token_status(ch.id, ch.token_path, oauth)
    long_uploads_status = resolve_long_uploads_status(
        ch.id,
        ch.token_path,
        oauth,
        auth_valid=auth.valid,
        stored_status=ch.long_uploads_status,
    )
    channel_out = ChannelOut(
        id=ch.id,
        name=ch.name,
        youtube_channel_id=ch.youtube_channel_id,
        custom_url=ch.custom_url,
        category=ch.category,
        token_path=ch.token_path,
        registry_path=ch.registry_path,
        auth=auth,
        publish=PublishConfigOut(
            timezone=ch.publish.timezone,
            hour=ch.publish.hour,
            interval_hours=ch.publish.interval_hours,
            uploads_per_day=ch.publish.uploads_per_day,
        ),
        pending_count=bundle.pending_count,
        uploaded_count=bundle.uploaded_count,
        failed_count=bundle.failed_count,
        long_uploads_status=long_uploads_status,
        verified=is_channel_verified(long_uploads_status),
    )
    queue_jobs = [job_view_to_out(j) for j in bundle.queue_jobs]
    uploading_jobs = [job_view_to_out(j) for j in bundle.uploading_jobs]
    uploaded_jobs = [job_view_to_out(j) for j in bundle.uploaded_jobs]
    return channel_out, queue_jobs, uploading_jobs, uploaded_jobs


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
    queue_jobs: list[JobOut] = []
    uploading_jobs: list[JobOut] = []
    uploaded_jobs: list[JobOut] = []
    if config.channels:
        with ThreadPoolExecutor(max_workers=min(8, len(config.channels))) as pool:
            futures = {
                pool.submit(_load_channel_bundle, ch, oauth, base=base): ch.id
                for ch in config.channels
            }
            by_id: dict[str, tuple[ChannelOut, list[JobOut], list[JobOut], list[JobOut]]] = {}
            for fut in as_completed(futures):
                ch_id = futures[fut]
                by_id[ch_id] = fut.result()
        for ch in config.channels:
            channel_out, ch_queue, ch_uploading, ch_uploaded = by_id[ch.id]
            channels.append(channel_out)
            queue_jobs.extend(ch_queue)
            uploading_jobs.extend(ch_uploading)
            uploaded_jobs.extend(ch_uploaded)

    response = DashboardResponse(
        config_uri=config_storage_uri(base),
        storage="r2" if remote_storage_enabled() else "local",
        categories=config.categories,
        channels=channels,
        queue_jobs=queue_jobs,
        uploading_jobs=uploading_jobs,
        uploaded_jobs=uploaded_jobs,
        jobs=queue_jobs,
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
        _verification_cache.clear()
    bump_cache("all")
