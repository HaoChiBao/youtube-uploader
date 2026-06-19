"""Upload a video to YouTube via the Data API v3 (OAuth installed-app flow)."""

from __future__ import annotations

import errno
import os
import socket
import time
from collections.abc import Callable
from pathlib import Path

# YouTube Data API v3 scopes used by this service:
# - youtube.upload:     insert videos, set thumbnails, schedule on upload (publishAt)
# - youtube.readonly:   channels.list, playlistItems.list, videos.list (channel add + uploader list)
# - youtube.force-ssl:  videos.update / delete (reschedule or fix metadata after upload)
#
# Not included (not needed for a standard channel uploader):
# - youtube                  (superset of upload+force-ssl; broader OAuth verification)
# - youtubepartner           (YouTube CMS / content-owner accounts only)
# - youtubepartner-channel-audit
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

DEFAULT_CATEGORY_ID = "10"
DEFAULT_PRIVACY = "private"
VALID_PRIVACY = ("private", "unlisted", "public")

MAX_THUMBNAIL_BYTES = 2_000_000


def _require_google_libs():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "YouTube upload needs google-api-python-client and google-auth-oauthlib.\n"
            "Install with: pip install ."
        ) from e
    return (
        Request,
        Credentials,
        InstalledAppFlow,
        build,
        HttpError,
        MediaFileUpload,
    )


def _prepare_thumbnail(thumbnail_path: Path) -> tuple[Path, Path | None]:
    """Return a thumbnail path that satisfies YouTube's 2 MB limit."""
    try:
        if thumbnail_path.stat().st_size <= MAX_THUMBNAIL_BYTES:
            return thumbnail_path, None
    except OSError:
        return thumbnail_path, None

    import tempfile

    from PIL import Image

    fd, tmp_name = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    tmp = Path(tmp_name)
    with Image.open(thumbnail_path) as im:
        img = im.convert("RGB")
    img.thumbnail((1280, 720))
    quality = 90
    while True:
        img.save(tmp, format="JPEG", quality=quality, optimize=True)
        if tmp.stat().st_size <= MAX_THUMBNAIL_BYTES or quality <= 40:
            break
        quality -= 10
    return tmp, tmp


def is_transient_upload_error(exc: BaseException) -> bool:
    """True for network blips and server-side errors worth retrying."""
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError, ConnectionResetError)):
        return True
    if isinstance(exc, OSError):
        retryable = {
            errno.ETIMEDOUT,
            errno.ECONNRESET,
            errno.ECONNABORTED,
            errno.EPIPE,
            errno.ECONNREFUSED,
            errno.EHOSTUNREACH,
            errno.ENETUNREACH,
        }
        if getattr(exc, "errno", None) in retryable:
            return True
    err_name = type(exc).__name__
    if err_name in {"TransportError", "HttpLib2Error"}:
        return True
    try:
        _Request, _Credentials, _Flow, _build, HttpError, _Media = _require_google_libs()
        if isinstance(exc, HttpError):
            status = int(getattr(exc.resp, "status", 0) or 0)
            return status in (408, 429, 500, 502, 503, 504)
    except RuntimeError:
        pass
    return False


def _credentials_need_reauth(creds) -> bool:
    """True when the token is missing a refresh token or any required scope."""
    if not creds or not creds.refresh_token:
        return True
    granted = set(creds.scopes or [])
    return not all(scope in granted for scope in SCOPES)


def _oauth_prompt(*, force_reauth: bool, oauth_prompt: str | None) -> str:
    """Pick a Google prompt that returns a refresh token (required for uploads)."""
    if oauth_prompt is not None:
        return oauth_prompt
    if force_reauth:
        return "select_account consent"
    return "consent"


def get_credentials(
    token_path: Path,
    *,
    client_secret: Path | None = None,
    client_config: dict | None = None,
    oauth_port: int = 8080,
    force_reauth: bool = False,
    oauth_prompt: str | None = None,
):
    """Load cached OAuth creds, refreshing or running the browser flow as needed.

    When ``force_reauth`` is True, always opens the browser so you can pick a
    Google account. Uses ``prompt=select_account consent`` so Google returns a
    refresh token (required for unattended uploads).
    """
    if client_config is None and client_secret is None:
        raise ValueError("Provide client_config (env) or client_secret (JSON file path).")

    Request, Credentials, InstalledAppFlow, _build, _HttpError, _Media = _require_google_libs()

    creds = None
    if not force_reauth and token_path.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            if _credentials_need_reauth(creds):
                creds = None
        except (ValueError, KeyError):
            creds = None

    if not creds or not creds.valid:
        if (
            not force_reauth
            and creds
            and creds.expired
            and creds.refresh_token
        ):
            creds.refresh(Request())
        else:
            if client_config is not None:
                flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
            oauth_kwargs: dict = {
                "access_type": "offline",
                "prompt": _oauth_prompt(force_reauth=force_reauth, oauth_prompt=oauth_prompt),
            }
            try:
                creds = flow.run_local_server(
                    port=oauth_port,
                    redirect_uri_trailing_slash=False,
                    **oauth_kwargs,
                )
            except OSError as e:
                winerr = getattr(e, "winerror", None)
                if winerr in (10013, 10048) or e.errno in (98, 10048):
                    raise RuntimeError(
                        f"Cannot start OAuth callback server on port {oauth_port}. "
                        "Another app is likely using that port (common on Windows with 8080). "
                        f"Fix: set GOOGLE_OAUTH_PORT=8765 in .env, add "
                        f"http://localhost:8765 and http://127.0.0.1:8765 to your Google Cloud "
                        "OAuth client redirect URIs, then run again."
                    ) from e
                raise
            if not creds.refresh_token:
                raise RuntimeError(
                    "Google did not return a refresh token. "
                    "Revoke this app at https://myaccount.google.com/permissions "
                    "(look for 'YouTube Uploader' or your Google Cloud project), "
                    "then run: uploader channel add"
                )
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def upload_video(
    video_path: Path,
    *,
    title: str,
    description: str,
    token_path: Path,
    client_secret: Path | None = None,
    client_config: dict | None = None,
    privacy: str = DEFAULT_PRIVACY,
    category_id: str = DEFAULT_CATEGORY_ID,
    tags: list[str] | None = None,
    made_for_kids: bool = False,
    thumbnail_path: Path | None = None,
    publish_at: str | None = None,
    oauth_port: int = 8080,
    on_progress: Callable[[float], None] | None = None,
) -> dict:
    """Upload video_path (resumable) and optionally set a custom thumbnail."""
    if privacy not in VALID_PRIVACY:
        raise ValueError(f"privacy must be one of {VALID_PRIVACY}, got {privacy!r}")
    if not video_path.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")

    _Request, _Credentials, _Flow, build, _HttpError, MediaFileUpload = _require_google_libs()
    creds = get_credentials(
        token_path,
        client_secret=client_secret,
        client_config=client_config,
        oauth_port=oauth_port,
    )
    youtube = build("youtube", "v3", credentials=creds)

    status: dict = {
        "privacyStatus": "private" if publish_at else privacy,
        "selfDeclaredMadeForKids": made_for_kids,
    }
    if publish_at:
        status["publishAt"] = publish_at

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": category_id,
        },
        "status": status,
    }
    if tags:
        body["snippet"]["tags"] = tags

    media = MediaFileUpload(str(video_path), chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status and on_progress:
            on_progress(status.progress())
    if on_progress:
        on_progress(1.0)

    video_id = response.get("id")
    if thumbnail_path and video_id and thumbnail_path.is_file():
        send_path, tmp_path = _prepare_thumbnail(thumbnail_path)
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(send_path)),
            ).execute()
        except Exception as e:  # noqa: BLE001 - thumbnail is best-effort
            response["_thumbnail_warning"] = str(e)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    return response


def upload_video_with_retry(
    video_path: Path,
    *,
    max_attempts: int = 3,
    retry_delay_sec: float = 30.0,
    on_retry: Callable[[int, int, BaseException], None] | None = None,
    **upload_kwargs,
) -> dict:
    """Call upload_video, retrying transient failures with linear backoff."""
    attempts = max(1, max_attempts)
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return upload_video(video_path, **upload_kwargs)
        except BaseException as e:
            last_error = e
            if attempt >= attempts or not is_transient_upload_error(e):
                raise
            if on_retry is not None:
                on_retry(attempt, attempts, e)
            time.sleep(retry_delay_sec * attempt)
    assert last_error is not None
    raise last_error
