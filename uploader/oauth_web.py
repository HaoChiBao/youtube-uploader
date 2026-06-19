"""Web OAuth flow for FastAPI (redirect-based, no local callback server)."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from uploader.channel_info import get_authorized_channel_info
from uploader.channel_store import (
    _PENDING_TOKEN,
    _channel_entry_dict,
    _config_base,
    _existing_youtube_ids,
    derive_channel_id,
    find_channel_index,
    make_unique_channel_id,
)
from uploader.channels import ChannelConfig, PublishConfig
from uploader.oauth import OAuthSettings
from uploader.state_store import init_channel_storage, read_raw_config, save_token, write_raw_config
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
    channel_id_override: str | None = None,
) -> ChannelConfig:
    """Save OAuth token and create or update a channel entry (after web callback)."""
    config_path = config_path.expanduser().resolve()
    base = _config_base(config_path)
    data = read_raw_config(config_path)

    _PENDING_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    _PENDING_TOKEN.write_text(creds_json, encoding="utf-8")

    info = get_authorized_channel_info(
        _PENDING_TOKEN,
        client_secret=oauth.client_secret_path,
        client_config=oauth.client_config,
        oauth_port=oauth.oauth_port,
    )

    if channel_id_override:
        channel_id = channel_id_override
        idx = next(
            (i for i, raw in enumerate(data.get("channels") or []) if raw.get("id") == channel_id),
            None,
        )
        if idx is None:
            raise KeyError(f"Channel not found: {channel_id}")
        token_loc = save_token(channel_id, creds_json, base=base)
        entry = data["channels"][idx]
        entry["name"] = info.title
        entry["youtube_channel_id"] = info.youtube_channel_id
        if info.custom_url:
            entry["custom_url"] = info.custom_url
        entry["token_path"] = token_loc
        write_raw_config(config_path, data)
        _PENDING_TOKEN.unlink(missing_ok=True)
        init_channel_storage(
            channel_id,
            base=base,
            name=info.title,
            youtube_channel_id=info.youtube_channel_id,
            custom_url=info.custom_url or entry.get("custom_url", ""),
        )
        return ChannelConfig(
            id=channel_id,
            name=info.title,
            token_path=token_loc,
            registry_path=entry.get("registry_path", ""),
            youtube_channel_id=info.youtube_channel_id,
            custom_url=info.custom_url,
            publish=publish or PublishConfig(),
        )

    base_id = derive_channel_id(info)
    existing = _existing_youtube_ids(data)
    channel_id = make_unique_channel_id(base_id, info.youtube_channel_id, existing)
    token_loc = save_token(channel_id, creds_json, base=base)
    _PENDING_TOKEN.unlink(missing_ok=True)

    init_channel_storage(
        channel_id,
        base=base,
        name=info.title,
        youtube_channel_id=info.youtube_channel_id,
        custom_url=info.custom_url,
    )

    entry = _channel_entry_dict(
        channel_id=channel_id,
        name=info.title,
        youtube_channel_id=info.youtube_channel_id,
        base=base,
        custom_url=info.custom_url,
        publish=publish,
    )

    idx = find_channel_index(data, info.youtube_channel_id)
    if idx is not None:
        data["channels"][idx] = entry
    else:
        data["channels"].append(entry)

    write_raw_config(config_path, data)

    return ChannelConfig(
        id=channel_id,
        name=info.title,
        token_path=token_loc,
        registry_path=entry["registry_path"],
        youtube_channel_id=info.youtube_channel_id,
        custom_url=info.custom_url,
        publish=publish or PublishConfig(),
    )


def credentials_to_json(creds) -> str:
    return creds.to_json()


def inspect_token_file(token_path: str | Path, *, client_secret, client_config) -> dict:
    """Check token without opening a browser."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    from uploader.object_storage import exists, read_text

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
            return {"has_token": True, "valid": True, "status": "refreshed"}
        except Exception:
            return {"has_token": True, "valid": False, "status": "refresh_failed"}

    return {"has_token": True, "valid": False, "status": "needs_reauth"}
