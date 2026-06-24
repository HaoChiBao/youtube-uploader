"""Distributed upload locks (R2 or local) so parallel workers never take the same job."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from uploader import bucket_layout
from uploader.object_storage import is_s3_uri, read_text, write_text


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def lock_location(channel_id: str, job_id: str, *, base: Path) -> str:
    return bucket_layout.resolve_location(bucket_layout.upload_lock_key(channel_id, job_id), base)


def _read_lock(uri: str) -> dict | None:
    try:
        text = read_text(uri)
    except Exception:
        return None
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _lock_expired(data: dict) -> bool:
    until = _parse_iso(str(data.get("leased_until", "")))
    if until is None:
        return True
    return until <= datetime.now(timezone.utc)


def acquire_upload_lock(
    channel_id: str,
    job_id: str,
    worker_id: str,
    *,
    base: Path,
    lease_seconds: int = 7200,
) -> bool:
    """Try to acquire an exclusive lock for channel/job. Returns True if this worker owns it."""
    uri = lock_location(channel_id, job_id, base=base)
    now = datetime.now(timezone.utc)
    payload = {
        "worker_id": worker_id,
        "channel_id": channel_id,
        "job_id": job_id,
        "started_at": _utc_now_iso(),
        "leased_until": (now + timedelta(seconds=lease_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    body = json.dumps(payload, ensure_ascii=False)

    if is_s3_uri(uri):
        from botocore.exceptions import ClientError

        from uploader.object_storage import _s3_client_for_uri, parse_s3_uri

        bucket, key = parse_s3_uri(uri)
        client = _s3_client_for_uri(uri)
        try:
            client.put_object(Bucket=bucket, Key=key, Body=body.encode("utf-8"), IfNoneMatch="*")
            return True
        except ClientError as e:
            code = str(e.response.get("Error", {}).get("Code", ""))
            if code not in ("PreconditionFailed", "412"):
                raise
        existing = _read_lock(uri)
        if existing and existing.get("worker_id") == worker_id:
            write_text(uri, body)
            return True
        if existing and not _lock_expired(existing):
            return False
        try:
            head = client.head_object(Bucket=bucket, Key=key)
            etag = head.get("ETag", "").strip('"')
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body.encode("utf-8"),
                IfMatch=etag,
            )
            return True
        except ClientError:
            return False

    path = Path(uri)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        return True
    except FileExistsError:
        existing = _read_lock(uri)
        if existing and existing.get("worker_id") == worker_id:
            path.write_text(body, encoding="utf-8")
            return True
        if existing and not _lock_expired(existing):
            return False
        path.write_text(body, encoding="utf-8")
        return True


def release_upload_lock(channel_id: str, job_id: str, *, base: Path, worker_id: str | None = None) -> None:
    """Drop lock file. If worker_id is set, only release when it matches."""
    uri = lock_location(channel_id, job_id, base=base)
    if worker_id:
        existing = _read_lock(uri)
        if existing and existing.get("worker_id") != worker_id:
            return
    if is_s3_uri(uri):
        from uploader.object_storage import _s3_client_for_uri, parse_s3_uri

        bucket, key = parse_s3_uri(uri)
        client = _s3_client_for_uri(uri)
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass
        return
    path = Path(uri)
    if path.is_file():
        path.unlink(missing_ok=True)


def refresh_upload_lock(
    channel_id: str,
    job_id: str,
    worker_id: str,
    *,
    base: Path,
    lease_seconds: int = 7200,
) -> None:
    """Extend lease for an active worker."""
    uri = lock_location(channel_id, job_id, base=base)
    existing = _read_lock(uri)
    if not existing or existing.get("worker_id") != worker_id:
        return
    now = datetime.now(timezone.utc)
    existing["leased_until"] = (now + timedelta(seconds=lease_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_text(uri, json.dumps(existing, ensure_ascii=False))
