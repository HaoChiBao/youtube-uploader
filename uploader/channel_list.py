"""List videos on the authorized YouTube channel via the Data API v3."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from uploader.youtube_client import get_credentials

_ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


@dataclass
class YouTubeVideoInfo:
    video_id: str
    title: str
    privacy_status: str
    publish_at: str | None
    url: str
    published_at: str = ""
    thumbnail_url: str = ""
    description: str = ""
    duration_seconds: int | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None

    @property
    def is_scheduled(self) -> bool:
        """True when the video is private and set to publish in the future."""
        if self.privacy_status != "private" or not self.publish_at:
            return False
        return _parse_api_datetime(self.publish_at) > datetime.now(timezone.utc)

    def publish_at_local(self) -> datetime | None:
        if not self.publish_at:
            return None
        return _parse_api_datetime(self.publish_at).astimezone()


def _parse_api_datetime(value: str) -> datetime:
    return parse_youtube_datetime(value)


def parse_youtube_datetime(value: str) -> datetime:
    """Parse YouTube API RFC3339 publishAt (Z or offset)."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def parse_iso8601_duration(value: str) -> int | None:
    """Parse YouTube contentDetails.duration (e.g. PT1H2M3S) to seconds."""
    if not value:
        return None
    match = _ISO_DURATION_RE.match(value.strip())
    if not match:
        return None
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def thumbnail_url_for_video(video_id: str, snippet_thumbs: dict | None = None) -> str:
    """Best available thumbnail URL for a video."""
    if snippet_thumbs:
        for key in ("medium", "high", "standard", "maxres", "default"):
            entry = snippet_thumbs.get(key) or {}
            url = entry.get("url") or ""
            if url:
                return url
    if video_id:
        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return ""


def fetch_scheduled_publish_datetimes(
    token_path: str | Path,
    *,
    client_secret: Path | None = None,
    client_config: dict | None = None,
    oauth_port: int = 8080,
) -> list[datetime]:
    """Return future scheduled publishAt times from the YouTube channel."""
    videos = list_channel_videos(
        token_path,
        client_secret=client_secret,
        client_config=client_config,
        scheduled_only=True,
        oauth_port=oauth_port,
    )
    return [parse_youtube_datetime(v.publish_at) for v in videos if v.publish_at]


def infer_schedule_interval_hours(scheduled: list[datetime]) -> float | None:
    """Median gap between consecutive scheduled publish times, in hours."""
    if len(scheduled) < 2:
        return None
    times = sorted(scheduled)
    gaps: list[float] = []
    for i in range(1, len(times)):
        delta = times[i] - times[i - 1]
        hours = delta.total_seconds() / 3600.0
        if hours >= 0.5:
            gaps.append(hours)
    if not gaps:
        return None
    gaps.sort()
    mid = len(gaps) // 2
    median = gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2.0
    return max(1.0, min(168.0, round(median, 2)))


def _build_youtube(
    token_path: str | Path,
    *,
    client_secret: Path | None = None,
    client_config: dict | None = None,
    oauth_port: int = 8080,
):
    from uploader.youtube_client import _require_google_libs

    _Request, _Credentials, _Flow, build, _HttpError, _Media = _require_google_libs()
    creds = get_credentials(
        token_path,
        client_secret=client_secret,
        client_config=client_config,
        oauth_port=oauth_port,
    )
    return build("youtube", "v3", credentials=creds)


def _uploads_playlist_id(youtube) -> str:
    response = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = response.get("items") or []
    if not items:
        raise RuntimeError("No YouTube channel found for the authorized account.")
    playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    if not playlist_id:
        raise RuntimeError("Could not resolve the channel uploads playlist.")
    return playlist_id


def _iter_upload_video_ids(youtube, playlist_id: str) -> list[str]:
    ids: list[str] = []
    page_token: str | None = None
    while True:
        request = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=page_token,
        )
        response = request.execute()
        for item in response.get("items") or []:
            video_id = item.get("contentDetails", {}).get("videoId")
            if video_id:
                ids.append(video_id)
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return ids


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _video_info_from_item(item: dict) -> YouTubeVideoInfo:
    video_id = item.get("id", "") or ""
    snippet = item.get("snippet") or {}
    status = item.get("status") or {}
    details = item.get("contentDetails") or {}
    stats = item.get("statistics") or {}
    publish_at = status.get("publishAt") or None
    return YouTubeVideoInfo(
        video_id=video_id,
        title=snippet.get("title") or video_id,
        privacy_status=status.get("privacyStatus") or "",
        publish_at=publish_at,
        url=f"https://youtu.be/{video_id}" if video_id else "",
        published_at=snippet.get("publishedAt") or "",
        thumbnail_url=thumbnail_url_for_video(video_id, snippet.get("thumbnails")),
        description=(snippet.get("description") or "")[:500],
        duration_seconds=parse_iso8601_duration(details.get("duration") or ""),
        view_count=_int_or_none(stats.get("viewCount")),
        like_count=_int_or_none(stats.get("likeCount")),
        comment_count=_int_or_none(stats.get("commentCount")),
    )


def list_channel_videos(
    token_path: str | Path,
    *,
    client_secret: Path | None = None,
    client_config: dict | None = None,
    scheduled_only: bool = False,
    oauth_port: int = 8080,
) -> list[YouTubeVideoInfo]:
    """Return metadata for every video on the OAuth-authorized channel."""
    youtube = _build_youtube(
        token_path,
        client_secret=client_secret,
        client_config=client_config,
        oauth_port=oauth_port,
    )
    playlist_id = _uploads_playlist_id(youtube)
    video_ids = _iter_upload_video_ids(youtube, playlist_id)

    videos: list[YouTubeVideoInfo] = []
    for offset in range(0, len(video_ids), 50):
        batch = video_ids[offset : offset + 50]
        response = (
            youtube.videos()
            .list(part="snippet,status,contentDetails,statistics", id=",".join(batch))
            .execute()
        )
        for item in response.get("items") or []:
            videos.append(_video_info_from_item(item))

    if scheduled_only:
        videos = [v for v in videos if v.is_scheduled]
        videos.sort(key=lambda v: _parse_api_datetime(v.publish_at or ""))
    else:
        # Newest first for Studio-like browsing (published_at, then scheduled publish_at).
        def sort_key(v: YouTubeVideoInfo) -> str:
            return v.published_at or v.publish_at or ""

        videos.sort(key=sort_key, reverse=True)

    return videos


def get_channel_video(
    token_path: str | Path,
    video_id: str,
    *,
    client_secret: Path | None = None,
    client_config: dict | None = None,
    oauth_port: int = 8080,
) -> YouTubeVideoInfo | None:
    """Fetch a single video owned by the authorized channel."""
    if not video_id:
        return None
    youtube = _build_youtube(
        token_path,
        client_secret=client_secret,
        client_config=client_config,
        oauth_port=oauth_port,
    )
    response = (
        youtube.videos()
        .list(part="snippet,status,contentDetails,statistics", id=video_id)
        .execute()
    )
    items = response.get("items") or []
    if not items:
        return None
    return _video_info_from_item(items[0])
