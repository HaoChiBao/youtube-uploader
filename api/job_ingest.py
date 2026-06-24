"""HTTP helpers for staging jobs into the upload queue."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from api.schemas import JobRegisterRequest, StagedJobOut
from uploader.channels import ChannelConfig
from uploader.job_defaults import JobDefaults
from uploader.job_metadata import JobMetadata
from uploader.job_store import StagedJob, register_job_from_uris, stage_job
from uploader.registry import STATUS_PENDING


def staged_job_to_out(staged: StagedJob) -> StagedJobOut:
    meta = staged.metadata
    return StagedJobOut(
        job_id=staged.job_id,
        channel_id=staged.channel_id,
        status=STATUS_PENDING,
        title=meta.title,
        description=meta.description,
        video_uri=staged.video_uri,
        thumbnail_uri=staged.thumbnail_uri,
        metadata_uri=staged.metadata_uri,
        queue_prefix=staged.job_prefix,
        uploaded_prefix=staged.uploaded_prefix,
        registry_path=staged.registry_path,
        privacy=meta.privacy,
        is_short=meta.is_short,
        tags=meta.tags,
    )


def _parse_tags(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    tags = [t.strip() for t in raw.split(",") if t.strip()]
    return tags or None


def _parse_bool_form(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def _parse_metadata_json(raw: str | None) -> JobMetadata | None:
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(422, f"Invalid metadata JSON: {e}") from e
    if not isinstance(data, dict):
        raise HTTPException(422, "metadata must be a JSON object")
    return JobMetadata.from_dict(data)


async def _save_upload(upload: UploadFile, dest: Path) -> None:
    content = await upload.read()
    if not content:
        raise HTTPException(400, f"Empty upload: {upload.filename or 'file'}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)


def _ingest_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(400, str(exc))
    if isinstance(exc, ValueError):
        msg = str(exc)
        if "already exists" in msg:
            return HTTPException(409, msg)
        return HTTPException(422, msg)
    if isinstance(exc, PermissionError):
        return HTTPException(502, str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(502, str(exc))
    return HTTPException(500, str(exc))


async def stage_job_from_upload(
    channel: ChannelConfig,
    *,
    video: UploadFile,
    title: str,
    description: str = "",
    thumbnail: UploadFile | None = None,
    job_id: str | None = None,
    base: Path,
    config_defaults: JobDefaults | None = None,
    privacy: str | None = None,
    is_short: bool | None = None,
    category_id: str | None = None,
    tags: list[str] | None = None,
    made_for_kids: bool | None = None,
    language: str | None = None,
    metadata: JobMetadata | None = None,
) -> StagedJobOut:
    if not title.strip():
        raise HTTPException(422, "title is required")

    with tempfile.TemporaryDirectory(prefix="uploader-stage-") as tmp:
        tmp_path = Path(tmp)
        video_suffix = Path(video.filename or "video.mp4").suffix or ".mp4"
        video_path = tmp_path / f"video{video_suffix}"
        await _save_upload(video, video_path)

        thumb_path: Path | None = None
        if thumbnail is not None and thumbnail.filename:
            thumb_suffix = Path(thumbnail.filename).suffix or ".png"
            thumb_path = tmp_path / f"thumbnail{thumb_suffix}"
            await _save_upload(thumbnail, thumb_path)

        try:
            staged = stage_job(
                channel,
                video_path=video_path,
                title=title.strip(),
                description=description,
                thumbnail_path=thumb_path,
                job_id=job_id.strip() if job_id else None,
                base=base,
                config_defaults=config_defaults,
                privacy=privacy,
                is_short=is_short,
                category_id=category_id,
                tags=tags,
                made_for_kids=made_for_kids,
                language=language or "",
                metadata=metadata,
            )
        except Exception as e:
            raise _ingest_http_error(e) from e

    return staged_job_to_out(staged)


def register_job_from_request(
    channel: ChannelConfig,
    body: JobRegisterRequest,
    *,
    base: Path,
    config_defaults: JobDefaults | None = None,
) -> tuple[StagedJobOut, bool]:
    metadata = None
    if body.metadata:
        metadata = JobMetadata.from_dict(body.metadata)

    try:
        staged, created = register_job_from_uris(
            channel,
            title=body.title,
            description=body.description,
            video_uri=body.video_uri,
            thumbnail_uri=body.thumbnail_uri or "",
            job_id=body.job_id,
            base=base,
            config_defaults=config_defaults,
            privacy=body.privacy,
            is_short=body.is_short,
            category_id=body.category_id,
            tags=body.tags,
            made_for_kids=body.made_for_kids,
            language=body.language or "",
            metadata=metadata,
        )
    except Exception as e:
        raise _ingest_http_error(e) from e

    return staged_job_to_out(staged), created


def parse_stage_form_fields(
    *,
    metadata_json: str | None,
    tags: str | None,
    is_short: str | None,
    made_for_kids: str | None,
) -> tuple[JobMetadata | None, list[str] | None, bool | None, bool | None]:
    metadata = _parse_metadata_json(metadata_json)
    return metadata, _parse_tags(tags), _parse_bool_form(is_short), _parse_bool_form(made_for_kids)
