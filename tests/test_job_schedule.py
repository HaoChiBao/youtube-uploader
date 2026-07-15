"""Tests for per-job scheduling helpers."""

from datetime import datetime, timedelta, timezone

from uploader.job_schedule import (
    apply_plan_publish_overrides,
    filter_pending_ready,
    normalize_schedule_at,
    resolve_job_publish_at,
    scheduled_publish_at_from_entry,
)
from uploader.registry import STATUS_PENDING, UploadEntry


def test_normalize_schedule_at_utc() -> None:
    assert normalize_schedule_at("2026-07-10T14:00:00Z") == "2026-07-10T14:00:00Z"


def test_filter_pending_ready_respects_upload_at() -> None:
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pending = [
        UploadEntry(id="now", channel_id="a", extra={}),
        UploadEntry(id="later", channel_id="a", extra={"upload_at": future}),
        UploadEntry(id="ready", channel_id="a", extra={"upload_at": past}),
    ]
    ready = filter_pending_ready(pending)
    ids = {e.id for e in ready}
    assert ids == {"now", "ready"}


def test_resolve_job_publish_at_uses_queue_preset() -> None:
    future = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = UploadEntry(
        id="j1",
        channel_id="a",
        status=STATUS_PENDING,
        publish_at=future,
    )
    assert scheduled_publish_at_from_entry(entry) == future
    resolved = resolve_job_publish_at(entry, "2026-07-01T09:00:00Z", no_schedule=False, override=None)
    assert resolved == future


def test_resolve_job_publish_at_drops_due_preset() -> None:
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = UploadEntry(
        id="j1",
        channel_id="a",
        status=STATUS_PENDING,
        publish_at=past,
        extra={"scheduled_publish_at": past},
    )
    assert resolve_job_publish_at(entry, "2026-07-01T09:00:00Z", no_schedule=False) == ""


def test_privacy_for_due_publish_forces_public() -> None:
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = UploadEntry(
        id="j1",
        channel_id="a",
        status=STATUS_PENDING,
        publish_at=past,
        extra={"scheduled_publish_at": past},
    )
    from uploader.job_schedule import privacy_for_due_publish

    assert privacy_for_due_publish(entry, "", privacy=None) == "public"
    assert privacy_for_due_publish(entry, "", privacy="private") == "public"
    assert privacy_for_due_publish(entry, "", privacy="unlisted") == "unlisted"
    assert privacy_for_due_publish(entry, past, privacy="private") == "private"


def test_apply_plan_publish_overrides_no_schedule() -> None:
    future = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = UploadEntry(id="j1", channel_id="a", status=STATUS_PENDING, publish_at=future)
    plan = apply_plan_publish_overrides([(entry, "2026-07-01T09:00:00Z")], no_schedule=True, publish_at_override=None)
    assert plan[0][1] == ""
