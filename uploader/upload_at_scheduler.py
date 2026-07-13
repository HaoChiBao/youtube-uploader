"""One-shot Cloud Scheduler jobs that dispatch a queued video at upload_at.

When ``UPLOADER_UPLOAD_AT_SCHEDULER`` is enabled and a job is registered with a
future ``upload_at``, a Cloud Scheduler HTTP job is created to call:

    POST /v1/channels/{channel}/jobs/{job_id}/dispatch-at

at that time. Past ``upload_at`` values skip scheduler creation (job is already
eligible for ``POST .../runs``). Deleting a queued job cancels its scheduler job.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from uploader.job_schedule import parse_schedule_at, upload_at_from_entry
from uploader.registry import STATUS_PENDING, UploadEntry, UploadRegistry

# Allow a small clock-skew window before treating a call as "too early".
EARLY_DISPATCH_GRACE = timedelta(seconds=60)
# Past times within this window are still considered "now" (ready, no cron).
PAST_READY_GRACE = timedelta(seconds=60)

_EXTRA_SCHEDULER_JOB = "upload_at_scheduler_job"
_EXTRA_SCHEDULER_STATUS = "upload_at_schedule_status"


@dataclass
class ScheduleResult:
    """Outcome of trying to arm a one-shot upload_at dispatcher."""

    status: str  # none | ready | scheduled | disabled | error | skipped
    scheduler_job: str = ""
    message: str = ""
    upload_at: str = ""


def upload_at_scheduler_enabled() -> bool:
    raw = os.environ.get("UPLOADER_UPLOAD_AT_SCHEDULER", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _project_id() -> str:
    return (
        os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        or os.environ.get("GOOGLE_PROJECT_ID", "").strip()
    )


def _scheduler_location() -> str:
    return (
        os.environ.get("UPLOADER_CLOUD_SCHEDULER_LOCATION", "").strip()
        or os.environ.get("UPLOADER_CLOUD_SCHEDULER_REGION", "").strip()
        or "us-central1"
    )


def _api_public_url() -> str:
    return (os.environ.get("UPLOADER_API_PUBLIC_URL", "") or "").strip().rstrip("/")


def _api_key() -> str:
    return (os.environ.get("UPLOADER_API_KEY", "") or "").strip()


def _oidc_sa() -> str:
    return (os.environ.get("UPLOADER_SCHEDULER_OIDC_SA", "") or "").strip()


def scheduler_job_name(channel_id: str, job_id: str) -> str:
    """Cloud Scheduler job id: 1–63 chars, [a-zA-Z0-9_-]."""
    raw = f"ua-{channel_id}-{job_id}"
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "-", raw)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-_")
    if not cleaned:
        cleaned = "ua-job"
    return cleaned[:63]


def cron_for_utc(when: datetime) -> str:
    """Build a unix-cron expression for ``when`` (UTC).

    Cloud Scheduler is minute-granularity. If ``when`` has non-zero seconds,
    round **up** to the next minute so the job never fires before ``upload_at``.
    """
    when = when.astimezone(timezone.utc)
    if when.second or when.microsecond:
        when = when.replace(second=0, microsecond=0) + timedelta(minutes=1)
    else:
        when = when.replace(second=0, microsecond=0)
    return f"{when.minute} {when.hour} {when.day} {when.month} *"


def classify_upload_at(
    upload_at: str | None,
    *,
    now: datetime | None = None,
) -> tuple[str, datetime | None]:
    """Classify upload_at relative to now.

    Returns ``(kind, when)`` where kind is:
      - ``empty`` — no upload_at
      - ``invalid`` — unparseable
      - ``ready`` — at or before now (within grace)
      - ``future`` — still in the future
    """
    if not upload_at or not str(upload_at).strip():
        return "empty", None
    try:
        when = parse_schedule_at(str(upload_at).strip())
    except ValueError:
        return "invalid", None
    now = now or datetime.now(timezone.utc)
    if when <= now + PAST_READY_GRACE:
        return "ready", when
    return "future", when


def _gcp_access_token() -> str:
    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError as e:
        raise RuntimeError(
            "google-auth is required for Cloud Scheduler (included with google-api-python-client)"
        ) from e

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(google.auth.transport.requests.Request())
    if not credentials.token:
        raise RuntimeError("Could not obtain GCP access token for Cloud Scheduler")
    return credentials.token


def _scheduler_parent() -> str:
    project = _project_id()
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT (or GOOGLE_PROJECT_ID) is required for upload_at scheduling")
    return f"projects/{project}/locations/{_scheduler_location()}"


def _http_json(method: str, url: str, body: dict | None = None) -> dict[str, Any]:
    token = _gcp_access_token()
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Cloud Scheduler {method} failed ({e.code}): {detail}") from e


def create_upload_at_scheduler_job(
    channel_id: str,
    job_id: str,
    upload_at: datetime,
) -> str:
    """Create (or replace) a one-shot Cloud Scheduler HTTP job. Returns job name."""
    base = _api_public_url()
    if not base:
        raise RuntimeError("UPLOADER_API_PUBLIC_URL is required for upload_at scheduling")

    name = scheduler_job_name(channel_id, job_id)
    parent = _scheduler_parent()
    full_name = f"{parent}/jobs/{name}"
    target_url = f"{base}/v1/channels/{channel_id}/jobs/{job_id}/dispatch-at"

    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = _api_key()
    if api_key:
        headers["X-API-Key"] = api_key

    import base64

    # Cloud Scheduler REST API expects base64-encoded body.
    http_target: dict[str, Any] = {
        "uri": target_url,
        "httpMethod": "POST",
        "headers": headers,
        "body": base64.b64encode(
            json.dumps({"source": "cloud_scheduler"}).encode("utf-8")
        ).decode("ascii"),
    }

    oidc = _oidc_sa()
    if oidc:
        http_target["oidcToken"] = {
            "serviceAccountEmail": oidc,
            "audience": base,
        }

    job_body = {
        "name": full_name,
        "description": f"One-shot upload_at dispatch for {channel_id}/{job_id}",
        "schedule": cron_for_utc(upload_at),
        "timeZone": "UTC",
        "httpTarget": http_target,
        "attemptDeadline": "300s",
    }

    # Replace if it already exists (re-register / reschedule).
    try:
        _http_json("DELETE", f"https://cloudscheduler.googleapis.com/v1/{full_name}")
    except RuntimeError as e:
        if "(404)" not in str(e):
            # Not found is fine; other errors still try create.
            pass

    created = _http_json(
        "POST",
        f"https://cloudscheduler.googleapis.com/v1/{parent}/jobs",
        job_body,
    )
    return str(created.get("name") or full_name)


def delete_upload_at_scheduler_job(
    channel_id: str,
    job_id: str,
    *,
    scheduler_job: str | None = None,
) -> bool:
    """Delete the Cloud Scheduler job if present. Returns True when deleted."""
    if not upload_at_scheduler_enabled():
        return False
    if not _project_id():
        return False

    if scheduler_job and scheduler_job.startswith("projects/"):
        full_name = scheduler_job
    else:
        name = scheduler_job or scheduler_job_name(channel_id, job_id)
        if "/" in name:
            full_name = name
        else:
            full_name = f"{_scheduler_parent()}/jobs/{name}"

    try:
        _http_json("DELETE", f"https://cloudscheduler.googleapis.com/v1/{full_name}")
        return True
    except RuntimeError as e:
        if "(404)" in str(e):
            return False
        # Best-effort cleanup — don't fail job deletion on scheduler errors.
        return False


def _set_registry_schedule_meta(
    registry: UploadRegistry,
    job_id: str,
    *,
    status: str,
    scheduler_job: str = "",
) -> None:
    def _upd(e: UploadEntry) -> None:
        extra = dict(e.extra or {})
        extra[_EXTRA_SCHEDULER_STATUS] = status
        if scheduler_job:
            extra[_EXTRA_SCHEDULER_JOB] = scheduler_job
        else:
            extra.pop(_EXTRA_SCHEDULER_JOB, None)
        e.extra = extra

    registry._update_entry(job_id, _upd)


def schedule_upload_at_dispatch(
    channel_id: str,
    job_id: str,
    upload_at: str | None,
    *,
    registry: UploadRegistry,
    created: bool = True,
    skip: bool = False,
) -> ScheduleResult:
    """Arm (or skip) a one-shot dispatcher for a newly queued job.

    ``skip`` is used when ``upload_now`` already dispatched, or on idempotent
    re-register of an existing job.
    """
    if skip or not created:
        kind, when = classify_upload_at(upload_at)
        return ScheduleResult(
            status="skipped",
            upload_at=when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else (upload_at or ""),
            message="Skipped (upload_now, or job already existed)",
        )

    kind, when = classify_upload_at(upload_at)
    if kind == "empty":
        return ScheduleResult(status="none", message="No upload_at set")
    if kind == "invalid":
        return ScheduleResult(
            status="error",
            upload_at=upload_at or "",
            message=f"Invalid upload_at: {upload_at!r}",
        )

    assert when is not None
    normalized = when.strftime("%Y-%m-%dT%H:%M:%SZ")

    if kind == "ready":
        _set_registry_schedule_meta(registry, job_id, status="ready")
        return ScheduleResult(
            status="ready",
            upload_at=normalized,
            message="upload_at is in the past; job is eligible for the next run (no cron created)",
        )

    if not upload_at_scheduler_enabled():
        _set_registry_schedule_meta(registry, job_id, status="disabled")
        return ScheduleResult(
            status="disabled",
            upload_at=normalized,
            message=(
                "upload_at stored as queue gate only; set UPLOADER_UPLOAD_AT_SCHEDULER=1 "
                "to create a Cloud Scheduler one-shot job"
            ),
        )

    try:
        full_name = create_upload_at_scheduler_job(channel_id, job_id, when)
    except Exception as e:
        _set_registry_schedule_meta(registry, job_id, status="error")
        return ScheduleResult(
            status="error",
            upload_at=normalized,
            message=f"Failed to create Cloud Scheduler job: {e}",
        )

    _set_registry_schedule_meta(
        registry,
        job_id,
        status="scheduled",
        scheduler_job=full_name,
    )
    return ScheduleResult(
        status="scheduled",
        scheduler_job=full_name,
        upload_at=normalized,
        message=f"Cloud Scheduler job armed for {normalized}",
    )


def cancel_upload_at_schedule(
    channel_id: str,
    job_id: str,
    *,
    registry: UploadRegistry | None = None,
    entry: UploadEntry | None = None,
) -> bool:
    """Cancel any Cloud Scheduler job tied to this queued video."""
    scheduler_job = None
    if entry is not None:
        scheduler_job = str((entry.extra or {}).get(_EXTRA_SCHEDULER_JOB) or "") or None
    elif registry is not None:
        found = registry.get(job_id)
        if found is not None:
            scheduler_job = str((found.extra or {}).get(_EXTRA_SCHEDULER_JOB) or "") or None
    return delete_upload_at_scheduler_job(channel_id, job_id, scheduler_job=scheduler_job)


def validate_dispatch_at(
    entry: UploadEntry,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Return (ok, reason) for a scheduled dispatch-at callback."""
    if entry.status != STATUS_PENDING:
        return False, f"Job status is {entry.status!r}, expected pending"
    upload_at = upload_at_from_entry(entry)
    if not upload_at:
        # Allow dispatch even without upload_at (manual/cron callback).
        return True, "ok"
    kind, when = classify_upload_at(upload_at, now=now)
    if kind == "invalid":
        return False, f"Invalid stored upload_at: {upload_at!r}"
    assert when is not None
    now = now or datetime.now(timezone.utc)
    if when > now + EARLY_DISPATCH_GRACE:
        return False, f"upload_at {upload_at} is still in the future"
    return True, "ok"
