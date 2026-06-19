"""Load Google OAuth client credentials from environment or a JSON file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OAuthSettings:
    """Resolved OAuth inputs for get_credentials / upload_video."""

    client_secret_path: Path | None
    client_config: dict | None
    oauth_port: int
    redirect_uri: str


def oauth_client_config_from_env() -> dict | None:
    """Build a Google client secrets dict from GOOGLE_* env vars, or None."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None

    redirect = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8080").strip()
    origins_raw = os.environ.get("GOOGLE_OAUTH_JAVASCRIPT_ORIGINS", "").strip()
    if origins_raw:
        javascript_origins = [o.strip() for o in origins_raw.split(",") if o.strip()]
    else:
        javascript_origins = [redirect.rstrip("/")]

    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "project_id": os.environ.get("GOOGLE_PROJECT_ID", "").strip(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [redirect],
            "javascript_origins": javascript_origins,
        }
    }


def resolve_oauth_settings(
    client_secret_path: Path,
    *,
    oauth_port: int = 8080,
) -> OAuthSettings:
    """Prefer env-based credentials; fall back to client_secret JSON file."""
    env_config = oauth_client_config_from_env()
    port = int(os.environ.get("GOOGLE_OAUTH_PORT", oauth_port))
    redirect = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", f"http://localhost:{port}").strip()

    if env_config:
        return OAuthSettings(
            client_secret_path=None,
            client_config=env_config,
            oauth_port=port,
            redirect_uri=redirect,
        )

    return OAuthSettings(
        client_secret_path=client_secret_path,
        client_config=None,
        oauth_port=port,
        redirect_uri=redirect,
    )


def oauth_is_configured(client_secret_path: Path) -> bool:
    """True when OAuth credentials are available via env or JSON file."""
    if oauth_client_config_from_env():
        return True
    return client_secret_path.is_file()
