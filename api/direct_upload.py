"""Upload a video directly to YouTube without queueing."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from api.schemas import DirectUploadOut
from uploader.channels import ChannelConfig
from uploader.job_defaults import JobDefaults
from api.job_ingest import _parse_bool_form, _parse_metadata_json, _parse_tags, _save_upload
from uploader.job_metadata import JobMetadata
from uploader.job_schedule import normalize_schedule_at
from uploader.oauth import OAuthSettings
from uploader.youtube_client import upload_video_with_retry


async def direct_upload_from_multipart(
    channel: ChannelConfig,
    *,
    video: UploadFile,
    title: str,
    description: str = "",
    thumbnail: UploadFile | None = None,
    privacy: str | None = None,
    is_short: bool | None = None,
    category_id: str | None = None,
    tags: list[str] | None = None,
    made_for_kids: bool | None = None,
    language: str | None = None,
    metadata: JobMetadata | None = None,
    publish_at: str | None = None,
    no_schedule: bool = False,
    config_defaults: JobDefaults | None = None,
    upload_retries: int = 3,
    retry_delay: float = 30.0,
    oauth: OAuthSettings | None = None,
) -> DirectUploadOut:
    if not title.strip():
        raise HTTPException(422, "title is required")

    effective_privacy = privacy or (metadata.privacy if metadata else None) or "private"
    effective_category = category_id or (metadata.category_id if metadata else None) or channel.category_id
    effective_tags = tags if tags is not None else (list(metadata.tags) if metadata else channel.default_tags or [])
    effective_mfk = made_for_kids if made_for_kids is not None else (
        metadata.made_for_kids if metadata else channel.made_for_kids
    )
    if is_short or (metadata and metadata.is_short):
        if not any(t.lower() == "shorts" for t in effective_tags):
            effective_tags = list(effective_tags) + ["Shorts"]

    youtube_publish_at = ""
    if not no_schedule and publish_at:
        youtube_publish_at = normalize_schedule_at(publish_at, timezone_name=channel.publish.timezone)

    with tempfile.TemporaryDirectory(prefix="uploader-direct-") as tmp:
        tmp_path = Path(tmp)
        video_suffix = Path(video.filename or "video.mp4").suffix or ".mp4"
        video_path = tmp_path / f"video{video_suffix}"
        await _save_upload(video, video_path)

        thumb_path: Path | None = None
        if thumbnail is not None and thumbnail.filename:
            thumb_suffix = Path(thumbnail.filename).suffix or ".png"
            thumb_path = tmp_path / f"thumbnail{thumb_suffix}"
            await _save_upload(thumbnail, thumb_path)

        if oauth is None:
            raise HTTPException(500, "OAuth settings not configured")

        try:
            response = upload_video_with_retry(
                video_path,
                title=title.strip(),
                description=description,
                token_path=channel.token_path,
                client_secret=oauth.client_secret_path,
                client_config=oauth.client_config,
                privacy=effective_privacy,
                category_id=effective_category,
                tags=effective_tags or None,
                made_for_kids=bool(effective_mfk),
                thumbnail_path=thumb_path,
                publish_at=youtube_publish_at or None,
                oauth_port=oauth.oauth_port,
                max_attempts=upload_retries,
                retry_delay_sec=retry_delay,
            )
        except Exception as e:
            raise HTTPException(502, f"YouTube upload failed: {e}") from e

    video_id = str(response.get("id") or "")
    url = f"https://youtu.be/{video_id}" if video_id else ""
    return DirectUploadOut(
        channel_id=channel.id,
        youtube_id=video_id,
        youtube_url=url,
        title=title.strip(),
        privacy=effective_privacy if not youtube_publish_at else "private",
        publish_at=youtube_publish_at,
    )


def parse_direct_upload_form(
    *,
    metadata_json: str | None,
    tags: str | None,
    is_short: str | None,
    made_for_kids: str | None,
) -> tuple[JobMetadata | None, list[str] | None, bool | None, bool | None]:
    metadata = _parse_metadata_json(metadata_json)
    return metadata, _parse_tags(tags), _parse_bool_form(is_short), _parse_bool_form(made_for_kids)
