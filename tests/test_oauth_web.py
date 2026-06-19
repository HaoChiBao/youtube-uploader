"""Tests for web OAuth PKCE flow."""

from __future__ import annotations

import pytest

from uploader.oauth import resolve_oauth_settings
from uploader.oauth_web import build_authorization_url


@pytest.fixture
def oauth_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "http://127.0.0.1:8000/v1/oauth/callback")
    return resolve_oauth_settings(tmp_path / "missing.json", oauth_port=8000)


def test_build_authorization_url_returns_code_verifier(oauth_settings):
    pytest.importorskip("google_auth_oauthlib")
    url, verifier = build_authorization_url(
        oauth_settings,
        redirect_uri="http://127.0.0.1:8000/v1/oauth/callback",
        state="test-state-nonce",
    )
    assert url.startswith("https://accounts.google.com")
    assert len(verifier) >= 43
