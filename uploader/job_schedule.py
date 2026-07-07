"""Per-job upload timing — queue pickup (upload_at) and YouTube publishAt."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from uploader.registry import STATUS_PENDING, UploadEntry


def normalize_schedule_at(
    value: str | None,
    *,
    timezone_name: str = "UTC",
) -> str:
    """Parse a schedule time and return RFC3339 UTC, or empty string."""
    if not value or not str(value).strip():
        return ""
    text = str(value).strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as e:
        raise ValueError(f"Invalid schedule time {value!r}: {e}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_schedule_at(value: str, *, timezone_name: str = "UTC") -> datetime:
    normalized = normalize_schedule_at(value, timezone_name=timezone_name)
    if not normalized:
        raise ValueError("empty schedule time")
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def upload_at_from_entry(entry: UploadEntry) -> str:
    extra = entry.extra or {}
    return str(extra.get("upload_at") or "")


def scheduled_publish_at_from_entry(entry: UploadEntry) -> str:
    """YouTube publishAt preset stored when the job was queued."""
    if entry.status != STATUS_PENDING:
        return ""
    extra = entry.extra or {}
    preset = str(extra.get("scheduled_publish_at") or "")
    if preset:
        return preset
    return entry.publish_at or ""


def filter_pending_ready(
    pending: list[UploadEntry],
    *,
    now: datetime | None = None,
    ignore_upload_at: bool = False,
) -> list[UploadEntry]:
    """Return pending jobs whose upload_at (if set) is in the past."""
    if ignore_upload_at:
        return list(pending)
    now = now or datetime.now(timezone.utc)
    ready: list[UploadEntry] = []
    for entry in pending:
        upload_at = upload_at_from_entry(entry)
        if not upload_at:
            ready.append(entry)
            continue
        try:
            when = parse_schedule_at(upload_at)
        except ValueError:
            ready.append(entry)
            continue
        if when <= now:
            ready.append(entry)
    return ready


def resolve_job_publish_at(
    entry: UploadEntry,
    computed: str,
    *,
    no_schedule: bool,
    override: str | None = None,
    timezone_name: str = "UTC",
) -> str:
    if no_schedule:
        return ""
    if override:
        return normalize_schedule_at(override, timezone_name=timezone_name)
    preset = scheduled_publish_at_from_entry(entry)
    if preset:
        return normalize_schedule_at(preset, timezone_name=timezone_name)
    return computed


def apply_plan_publish_overrides(
    plan: list[tuple[UploadEntry, str]],
    *,
    no_schedule: bool,
    publish_at_override: str | None = None,
    timezone_name: str = "UTC",
) -> list[tuple[UploadEntry, str]]:
    return [
        (
            entry,
            resolve_job_publish_at(
                entry,
                computed,
                no_schedule=no_schedule,
                override=publish_at_override,
                timezone_name=timezone_name,
            ),
        )
        for entry, computed in plan
    ]
