"""Tests for API token + dashboard session auth."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import create_session, verify_login_secret


def test_verify_login_accepts_password_or_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UPLOADER_DASHBOARD_PASSWORD", "pw")
    monkeypatch.setenv("UPLOADER_API_KEY", "tok")
    assert verify_login_secret("pw")
    assert verify_login_secret("tok")
    assert not verify_login_secret("wrong")


def test_session_roundtrip(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UPLOADER_API_KEY", "tok")
    monkeypatch.setenv("UPLOADER_DASHBOARD_PASSWORD", "pw")
    token = create_session()
    from api.auth import _unsign

    data = _unsign(token)
    assert data is not None
    assert data.get("kind") == "dashboard"


def test_session_unsign_when_hmac_contains_dot(monkeypatch: pytest.MonkeyPatch):
    """Regression: binary HMAC bytes can include 0x2e ('.'); must not use rsplit."""
    import base64
    import json
    import time

    monkeypatch.setenv("UPLOADER_SESSION_SECRET", "test-secret")
    from api import auth

    payload = json.dumps(
        {"kind": "dashboard", "exp": int(time.time()) + 3600},
        separators=(",", ":"),
    ).encode("utf-8")
    # Craft a digest that contains '.' so rsplit-based parsing would break.
    sig = b"a" * 15 + b"." + b"b" * 16
    assert len(sig) == 32
    token = base64.urlsafe_b64encode(payload + b"." + sig).decode("ascii")

    monkeypatch.setattr(
        auth.hmac,
        "new",
        lambda *a, **k: type("H", (), {"digest": staticmethod(lambda: sig)})(),
    )
    data = auth._unsign(token)
    assert data is not None
    assert data.get("kind") == "dashboard"


def test_session_cookie_not_secure_on_http(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UPLOADER_SESSION_SECURE", "1")
    from starlette.requests import Request

    from api.auth import session_cookie_secure

    scope = {"type": "http", "scheme": "http", "path": "/", "headers": []}
    request = Request(scope)
    assert session_cookie_secure(request) is False
