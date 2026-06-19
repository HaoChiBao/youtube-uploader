"""FastAPI dependencies."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from uploader.channels import AppConfig, resolve_channel
from uploader.oauth import OAuthSettings, oauth_is_configured, resolve_oauth_settings
from uploader.state_store import config_base_from_path, config_storage_uri, remote_storage_enabled

from api.cache import get_cached_config

load_dotenv(find_dotenv(usecwd=True))


def config_path() -> Path:
    env_path = os.environ.get("UPLOADER_CONFIG")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return Path("config/channels.yaml").expanduser().resolve()


def get_app_config() -> AppConfig:
    """Load channels.yaml (cached; invalidated on config/registry/token changes)."""
    return get_cached_config(config_path())


def get_storage_base() -> Path:
    return config_base_from_path(config_path())


def get_config_uri() -> str:
    return config_storage_uri(get_storage_base())


def get_storage_backend() -> str:
    return "r2" if remote_storage_enabled() else "local"

def get_oauth_settings() -> OAuthSettings:
    config = get_app_config()
    return resolve_oauth_settings(
        config.google.client_secret_path,
        oauth_port=config.google.oauth_port,
    )


def api_public_base() -> str:
    return os.environ.get("UPLOADER_API_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")


def resolve_channel_ref(ref: str):
    return resolve_channel(get_app_config(), ref)


def oauth_configured() -> bool:
    config = get_app_config()
    return oauth_is_configured(config.google.client_secret_path)
