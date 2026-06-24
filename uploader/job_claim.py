"""Claim pending jobs for parallel upload workers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from uploader.job_lock import acquire_upload_lock, release_upload_lock
from uploader.registry import (
    STATUS_PENDING,
    STATUS_UPLOADING,
    UploadEntry,
    UploadRegistry,
)


@dataclass
class ClaimResult:
    claimed: bool
    entry: UploadEntry | None = None
    worker_id: str = ""
    reason: str = ""


def try_claim_job(
    registry: UploadRegistry,
    channel_id: str,
    job_id: str,
    worker_id: str,
    *,
    base: Path,
    publish_at: str = "",
    lease_seconds: int = 7200,
) -> ClaimResult:
    """Acquire lock + mark registry uploading. Safe for parallel dispatch."""
    entry = registry.get(job_id)
    if entry is None:
        return ClaimResult(False, reason="job not found")
    if entry.channel_id != channel_id:
        return ClaimResult(False, reason="channel mismatch")

    if entry.status == STATUS_UPLOADING:
        owner = (entry.extra or {}).get("upload_worker_id", "")
        if owner == worker_id:
            return ClaimResult(True, entry=entry, worker_id=worker_id)
        if not UploadRegistry.upload_stale(entry):
            return ClaimResult(False, reason="already uploading")
    elif entry.status != STATUS_PENDING:
        return ClaimResult(False, reason=f"status is {entry.status}")

    if not acquire_upload_lock(channel_id, job_id, worker_id, base=base, lease_seconds=lease_seconds):
        return ClaimResult(False, reason="lock held by another worker")

    entry = registry.get(job_id)
    if entry is None:
        release_upload_lock(channel_id, job_id, base=base, worker_id=worker_id)
        return ClaimResult(False, reason="job disappeared")

    if entry.status == STATUS_UPLOADING:
        owner = (entry.extra or {}).get("upload_worker_id", "")
        if owner != worker_id and not UploadRegistry.upload_stale(entry):
            release_upload_lock(channel_id, job_id, base=base, worker_id=worker_id)
            return ClaimResult(False, reason="race: already uploading")
    elif entry.status != STATUS_PENDING:
        release_upload_lock(channel_id, job_id, base=base, worker_id=worker_id)
        return ClaimResult(False, reason=f"race: status is {entry.status}")

    registry.mark_uploading(job_id, worker_id=worker_id, publish_at=publish_at)
    entry = registry.get(job_id)
    return ClaimResult(True, entry=entry, worker_id=worker_id)


def release_job_claim(
    registry: UploadRegistry,
    channel_id: str,
    job_id: str,
    worker_id: str,
    *,
    base: Path,
    reset_to_pending: bool = False,
) -> None:
    release_upload_lock(channel_id, job_id, base=base, worker_id=worker_id)
    if reset_to_pending:
        registry.reset_upload_to_pending(job_id)


def cancel_upload_job(
    registry: UploadRegistry,
    channel_id: str,
    job_id: str,
    *,
    base: Path,
) -> UploadEntry:
    """Stop an in-flight upload and return the job to the pending queue."""
    entry = registry.get(job_id)
    if entry is None:
        raise ValueError(f"Job not found: {job_id}")
    if entry.channel_id != channel_id:
        raise ValueError("channel mismatch")
    if entry.status != STATUS_UPLOADING:
        raise ValueError(f"Job {job_id} is not uploading (status={entry.status})")

    release_upload_lock(channel_id, job_id, base=base, worker_id=None)
    registry.reset_upload_to_pending(job_id)
    restored = registry.get(job_id)
    if restored is None:
        raise ValueError(f"Job {job_id} disappeared after cancel")
    return restored
