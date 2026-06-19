"""Fetch metadata about the OAuth-authorized YouTube channel."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from uploader.youtube_client import get_credentials


@dataclass
class AuthorizedChannelInfo:
    youtube_channel_id: str
    title: str
    custom_url: str = ""


def get_authorized_channel_info(
    token_path: Path,
    *,
    client_secret: Path | None = None,
    client_config: dict | None = None,
    oauth_port: int = 8080,
    creds=None,
) -> AuthorizedChannelInfo:
    """Return the YouTube channel tied to the stored OAuth token."""
    from uploader.youtube_client import _require_google_libs

    _Request, _Credentials, _Flow, build, _HttpError, _Media = _require_google_libs()
    if creds is None:
        creds = get_credentials(
            token_path,
            client_secret=client_secret,
            client_config=client_config,
            oauth_port=oauth_port,
        )
    youtube = build("youtube", "v3", credentials=creds)
    response = youtube.channels().list(part="snippet", mine=True).execute()
    items = response.get("items") or []
    if not items:
        raise RuntimeError("No YouTube channel found for this Google account.")

    item = items[0]
    snippet = item.get("snippet") or {}
    return AuthorizedChannelInfo(
        youtube_channel_id=item.get("id", ""),
        title=snippet.get("title") or item.get("id", ""),
        custom_url=snippet.get("customUrl") or "",
    )
