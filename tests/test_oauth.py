"""Tests for OAuth env configuration."""

from __future__ import annotations

import os

from uploader.oauth import oauth_client_config_from_env, oauth_is_configured, resolve_oauth_settings


def test_oauth_client_config_from_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_PROJECT_ID", "my-project")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8080")
    monkeypatch.setenv(
        "GOOGLE_OAUTH_JAVASCRIPT_ORIGINS",
        "http://localhost:8080,http://127.0.0.1:8080",
    )

    cfg = oauth_client_config_from_env()
    assert cfg is not None
    web = cfg["web"]
    assert web["client_id"] == "id.apps.googleusercontent.com"
    assert web["client_secret"] == "secret"
    assert web["redirect_uris"] == ["http://localhost:8080"]
    assert web["javascript_origins"] == ["http://localhost:8080", "http://127.0.0.1:8080"]


def test_oauth_client_config_from_env_missing_returns_none(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    assert oauth_client_config_from_env() is None


def test_resolve_oauth_settings_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_OAUTH_PORT", "9090")

    settings = resolve_oauth_settings(tmp_path / "missing.json")
    assert settings.client_config is not None
    assert settings.client_secret_path is None
    assert settings.oauth_port == 9090


def test_oauth_is_configured_with_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    assert oauth_is_configured(tmp_path / "missing.json") is True


def test_oauth_is_configured_with_file(tmp_path):
    secret = tmp_path / "client_secret.json"
    secret.write_text("{}", encoding="utf-8")
    for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        os.environ.pop(key, None)
    assert oauth_is_configured(secret) is True
