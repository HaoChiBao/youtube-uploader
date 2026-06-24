"""Web OAuth flow for FastAPI (redirect-based, no local callback server)."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from uploader.channel_store import (
    OAuthRegistrationResult,
    register_oauth_channel,
)
from uploader.channels import PublishConfig
from uploader.oauth import OAuthSettings
from uploader.youtube_client import SCOPES, _credentials_need_reauth, _oauth_prompt


def _require_flow():
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError as e:
        raise RuntimeError("Install API deps: pip install '.[api]'") from e
    return Flow


def api_redirect_uri(oauth: OAuthSettings, *, api_base: str) -> str:
    """OAuth redirect for the API callback (override CLI redirect when using web flow)."""
    base = api_base.rstrip("/")
    return f"{base}/v1/oauth/callback"


def create_oauth_flow(
    oauth: OAuthSettings,
    *,
    redirect_uri: str,
    code_verifier: str | None = None,
):
    Flow = _require_flow()
    kwargs: dict = {"redirect_uri": redirect_uri}
    if code_verifier is not None:
        kwargs["code_verifier"] = code_verifier
        kwargs["autogenerate_code_verifier"] = False
    if oauth.client_config is not None:
        return Flow.from_client_config(
            oauth.client_config,
            scopes=SCOPES,
            **kwargs,
        )
    if oauth.client_secret_path and oauth.client_secret_path.is_file():
        return Flow.from_client_secrets_file(
            str(oauth.client_secret_path),
            scopes=SCOPES,
            **kwargs,
        )
    raise ValueError("OAuth not configured")


def build_authorization_url(
    oauth: OAuthSettings,
    *,
    redirect_uri: str,
    state: str,
    force_reauth: bool = True,
) -> tuple[str, str]:
    """Return (authorization_url, code_verifier). Store verifier for the callback."""
    flow = create_oauth_flow(oauth, redirect_uri=redirect_uri)
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt=_oauth_prompt(force_reauth=force_reauth, oauth_prompt=None),
        state=state,
    )
    if not flow.code_verifier:
        raise RuntimeError("OAuth PKCE code_verifier was not generated")
    return url, flow.code_verifier


def exchange_code_for_credentials(
    oauth: OAuthSettings,
    *,
    redirect_uri: str,
    code: str,
    state: str,
    expected_state: str,
    code_verifier: str,
):
    if state != expected_state:
        raise ValueError("Invalid OAuth state")
    flow = create_oauth_flow(oauth, redirect_uri=redirect_uri, code_verifier=code_verifier)
    flow.fetch_token(code=code)
    creds = flow.credentials
    if not creds.refresh_token:
        raise RuntimeError(
            "Google did not return a refresh token. Revoke app access at "
            "https://myaccount.google.com/permissions and try again with consent."
        )
    return creds


@dataclass
class OAuthState:
    nonce: str
    mode: Literal["add", "reauth"]
    channel_id: str = ""


def new_oauth_state(*, mode: Literal["add", "reauth"] = "add", channel_id: str = "") -> OAuthState:
    return OAuthState(nonce=secrets.token_urlsafe(32), mode=mode, channel_id=channel_id)


def register_channel_from_credentials(
    oauth: OAuthSettings,
    creds_json: str,
    *,
    config_path: Path,
    publish: PublishConfig | None = None,
    category: str = "",
    channel_id_override: str | None = None,
) -> OAuthRegistrationResult:
    """Save OAuth token and create or update a channel entry (after web callback)."""
    return register_oauth_channel(
        creds_json,
        config_path=config_path,
        reauth_channel_id=channel_id_override,
        publish=publish,
        category=category,
        oauth=oauth,
    )


def credentials_to_json(creds) -> str:
    return creds.to_json()


def inspect_token_file(token_path: str | Path, *, client_secret, client_config) -> dict:
    """Check token without opening a browser; refresh and persist when possible."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    from uploader.cache_signals import bump as bump_cache
    from uploader.object_storage import exists, read_text, write_text

    loc = str(token_path)
    if not exists(loc):
        return {"has_token": False, "valid": False, "status": "missing"}

    try:
        text = read_text(loc)
        if not text.strip():
            return {"has_token": False, "valid": False, "status": "empty"}
        creds = Credentials.from_authorized_user_info(json.loads(text), SCOPES)
    except (ValueError, KeyError, json.JSONDecodeError):
        return {"has_token": True, "valid": False, "status": "invalid"}

    if _credentials_need_reauth(creds):
        return {"has_token": True, "valid": False, "status": "needs_reauth"}

    if creds.valid:
        return {"has_token": True, "valid": True, "status": "ok"}

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            write_text(loc, creds.to_json())
            bump_cache("tokens")
            return {"has_token": True, "valid": True, "status": "ok"}
        except Exception:
            return {"has_token": True, "valid": False, "status": "refresh_failed"}

    return {"has_token": True, "valid": False, "status": "needs_reauth"}
