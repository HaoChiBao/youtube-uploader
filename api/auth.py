"""API token + dashboard session authentication."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from fastapi import Header, HTTPException, Request, Response

SESSION_COOKIE = "uploader_session"
SESSION_MAX_AGE_SEC = max(3600, int(os.environ.get("UPLOADER_SESSION_DAYS", "7")) * 86400)


def api_key() -> str:
    return os.environ.get("UPLOADER_API_KEY", "").strip()


def dashboard_password() -> str:
    return os.environ.get("UPLOADER_DASHBOARD_PASSWORD", "").strip()


def auth_enabled() -> bool:
    return bool(api_key() or dashboard_password())


def api_key_required() -> bool:
    return bool(api_key())


def dashboard_auth_required() -> bool:
    return bool(dashboard_password())


def _session_secret() -> bytes:
    explicit = os.environ.get("UPLOADER_SESSION_SECRET", "").strip()
    if explicit:
        return explicit.encode("utf-8")
    material = f"{api_key()}|{dashboard_password()}".encode("utf-8")
    if material == b"|":
        return secrets.token_bytes(32)
    return hashlib.sha256(material).digest()


def _sign(payload: bytes) -> str:
    sig = hmac.new(_session_secret(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + b"." + sig).decode("ascii")


def _unsign(token: str) -> dict[str, Any] | None:
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        # HMAC digests are binary and may contain b"." — never rsplit on that.
        # Format is always: payload + b"." + 32-byte sha256 digest.
        if len(raw) < 33 or raw[-33:-32] != b".":
            return None
        payload, sig = raw[:-33], raw[-32:]
        expected = hmac.new(_session_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(payload.decode("utf-8"))
        exp = int(data.get("exp", 0))
        if exp and time.time() > exp:
            return None
        return data
    except Exception:
        return None


def create_session(*, kind: str = "dashboard") -> str:
    payload = json.dumps(
        {"kind": kind, "exp": int(time.time()) + SESSION_MAX_AGE_SEC},
        separators=(",", ":"),
    ).encode("utf-8")
    return _sign(payload)


def session_cookie_secure(request: Request | None = None) -> bool:
    secure_env = os.environ.get("UPLOADER_SESSION_SECURE", "").strip().lower() in ("1", "true", "yes")
    if not secure_env:
        return False
    if request is None:
        return True
    forwarded = request.headers.get("x-forwarded-proto", "")
    if forwarded:
        return forwarded.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def set_session_cookie(response: Response, token: str, *, request: Request | None = None) -> None:
    secure = session_cookie_secure(request)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE_SEC,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response: Response, *, request: Request | None = None) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=session_cookie_secure(request),
        samesite="lax",
    )


def session_from_request(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    return _unsign(token)


def extract_api_key(request: Request) -> str | None:
    header_key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
    if header_key:
        return header_key.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def is_public_path(path: str, method: str) -> bool:
    if path in ("/health", "/v1/health"):
        return True
    if path == "/" and method == "GET":
        return True
    if path == "/v1/auth/session" and method == "GET":
        return True
    if path == "/login" and method in ("GET", "POST"):
        return True
    if path == "/logout" and method == "POST":
        return True
    if path == "/v1/oauth/callback":
        return True
    return False


def request_is_authenticated(request: Request) -> bool:
    if not auth_enabled():
        return True
    if session_from_request(request):
        return True
    key = extract_api_key(request)
    expected = api_key()
    if expected and key and secrets.compare_digest(key, expected):
        return True
    return False


def is_ui_path(path: str) -> bool:
    if path in ("/", "/login", "/docs", "/openapi.json", "/redoc"):
        return True
    return path.startswith("/static/")


def authenticate_request(request: Request) -> str | None:
    """Return auth method used, or None if not authenticated."""
    if not auth_enabled():
        return "open"

    if is_public_path(request.url.path, request.method):
        return "public"

    session = session_from_request(request)
    if session:
        return "session"

    key = extract_api_key(request)
    expected = api_key()
    if expected and key and secrets.compare_digest(key, expected):
        return "api_key"

    return None


def verify_login_secret(secret: str) -> bool:
    secret = secret.strip()
    if not secret:
        return False
    pw = dashboard_password()
    if pw and secrets.compare_digest(secret, pw):
        return True
    key = api_key()
    if key and secrets.compare_digest(secret, key):
        return True
    return False


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """Legacy FastAPI dependency — prefer AuthMiddleware."""
    if not api_key():
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, api_key()):
        raise HTTPException(401, "Invalid or missing API key (X-API-Key header)")


def auth_status() -> dict[str, Any]:
    return {
        "enabled": auth_enabled(),
        "api_key_required": api_key_required(),
        "dashboard_password_required": dashboard_auth_required(),
        "session_cookie": SESSION_COOKIE,
        "api_key_header": "X-API-Key",
        "bearer_supported": True,
    }
