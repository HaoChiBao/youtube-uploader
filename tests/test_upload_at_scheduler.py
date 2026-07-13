"""Unit tests for upload_at Cloud Scheduler helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from uploader.registry import STATUS_PENDING, STATUS_UPLOADING, UploadEntry, UploadRegistry
from uploader.upload_at_scheduler import (
    classify_upload_at,
    cron_for_utc,
    schedule_upload_at_dispatch,
    scheduler_job_name,
    validate_dispatch_at,
)


def test_scheduler_job_name_sanitizes() -> None:
    name = scheduler_job_name("justcavefire", "mv_2026-08-01T12:00:00Z")
    assert name.startswith("ua-justcavefire-")
    assert len(name) <= 63
    assert all(c.isalnum() or c in "-_" for c in name)


def test_cron_for_utc() -> None:
    when = datetime(2026, 8, 1, 6, 30, tzinfo=timezone.utc)
    assert cron_for_utc(when) == "30 6 1 8 *"


def test_classify_upload_at_past_ready_future() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    kind, when = classify_upload_at("2026-07-13T11:00:00Z", now=now)
    assert kind == "ready"
    assert when is not None

    kind, when = classify_upload_at("2026-07-13T12:00:30Z", now=now)
    assert kind == "ready"  # within 60s grace

    kind, when = classify_upload_at("2026-07-14T12:00:00Z", now=now)
    assert kind == "future"
    assert when is not None

    kind, _ = classify_upload_at("", now=now)
    assert kind == "empty"

    kind, _ = classify_upload_at("not-a-date", now=now)
    assert kind == "invalid"


def test_validate_dispatch_at_edge_cases() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    pending = UploadEntry(
        id="j1",
        channel_id="c",
        status=STATUS_PENDING,
        extra={"upload_at": "2026-07-13T11:00:00Z"},
    )
    ok, _ = validate_dispatch_at(pending, now=now)
    assert ok

    future = UploadEntry(
        id="j2",
        channel_id="c",
        status=STATUS_PENDING,
        extra={"upload_at": "2026-07-14T12:00:00Z"},
    )
    ok, reason = validate_dispatch_at(future, now=now)
    assert not ok
    assert "future" in reason

    uploading = UploadEntry(
        id="j3",
        channel_id="c",
        status=STATUS_UPLOADING,
        extra={"upload_at": "2026-07-13T11:00:00Z"},
    )
    ok, reason = validate_dispatch_at(uploading, now=now)
    assert not ok
    assert "uploading" in reason


def test_schedule_upload_at_past_is_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UPLOADER_UPLOAD_AT_SCHEDULER", raising=False)
    reg_path = tmp_path / "reg.txt"
    reg = UploadRegistry(reg_path)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    reg.append(
        UploadEntry(id="pastjob", channel_id="c", status=STATUS_PENDING, extra={"upload_at": past})
    )
    result = schedule_upload_at_dispatch("c", "pastjob", past, registry=reg, created=True)
    assert result.status == "ready"
    assert "past" in result.message.lower()
    entry = reg.get("pastjob")
    assert entry is not None
    assert entry.extra.get("upload_at_schedule_status") == "ready"


def test_schedule_upload_at_future_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UPLOADER_UPLOAD_AT_SCHEDULER", raising=False)
    reg = UploadRegistry(tmp_path / "reg.txt")
    future = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    reg.append(
        UploadEntry(id="fut", channel_id="c", status=STATUS_PENDING, extra={"upload_at": future})
    )
    result = schedule_upload_at_dispatch("c", "fut", future, registry=reg, created=True)
    assert result.status == "disabled"
    assert "UPLOADER_UPLOAD_AT_SCHEDULER" in result.message


def test_schedule_upload_at_creates_scheduler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UPLOADER_UPLOAD_AT_SCHEDULER", "1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-proj")
    monkeypatch.setenv("UPLOADER_API_PUBLIC_URL", "https://uploader.example.com")
    monkeypatch.setenv("UPLOADER_API_KEY", "secret")

    calls: list[tuple[str, str]] = []

    def fake_http(method: str, url: str, body: dict | None = None):
        calls.append((method, url))
        if method == "POST":
            return {"name": "projects/demo-proj/locations/us-central1/jobs/ua-c-fut"}
        return {}

    monkeypatch.setattr(
        "uploader.upload_at_scheduler._http_json",
        fake_http,
    )

    reg = UploadRegistry(tmp_path / "reg.txt")
    future = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    reg.append(
        UploadEntry(id="fut", channel_id="c", status=STATUS_PENDING, extra={"upload_at": future})
    )
    result = schedule_upload_at_dispatch("c", "fut", future, registry=reg, created=True)
    assert result.status == "scheduled"
    assert "ua-c-fut" in result.scheduler_job
    assert any(m == "POST" for m, _ in calls)
    entry = reg.get("fut")
    assert entry is not None
    assert entry.extra.get("upload_at_schedule_status") == "scheduled"
