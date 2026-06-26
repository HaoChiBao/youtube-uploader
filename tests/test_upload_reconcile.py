"""Tests for upload reconcile / stuck-job cleanup."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from uploader.channels import AppConfig, ChannelConfig, GoogleConfig
from uploader.oauth import OAuthSettings
from uploader.registry import STATUS_UPLOADED, STATUS_UPLOADING, UploadEntry, UploadRegistry
from uploader.upload_reconcile import _looks_complete, reconcile_uploads


def _channel(tmp_path: Path) -> ChannelConfig:
    return ChannelConfig(
        id="ch1",
        name="Test",
        token_path=str(tmp_path / "secrets/ch1/youtube_token.json"),
        registry_path=str(tmp_path / "state/ch1/upload_registry.txt"),
    )


def _config(ch: ChannelConfig) -> AppConfig:
    return AppConfig(channels=[ch], google=GoogleConfig())


def _oauth() -> OAuthSettings:
    return OAuthSettings(
        client_secret_path=None,
        client_config={},
        oauth_port=8080,
        redirect_uri="http://localhost:8080",
    )


def test_looks_complete_done_phase() -> None:
    entry = UploadEntry(id="j1", channel_id="ch1", extra={"upload_phase": "done", "upload_progress": 100})
    assert _looks_complete(entry) is True


def test_looks_complete_high_progress_uploading() -> None:
    entry = UploadEntry(
        id="j1",
        channel_id="ch1",
        extra={
            "upload_phase": "uploading",
            "upload_progress": 93,
            "upload_message": "YouTube upload finished",
        },
    )
    assert _looks_complete(entry) is True


def test_reconcile_resets_stale_uploading(tmp_path: Path, monkeypatch) -> None:
    ch = _channel(tmp_path)
    ch.registry_path = str(tmp_path / "registry.txt")
    reg_path = Path(ch.registry_path)
    reg_path.parent.mkdir(parents=True, exist_ok=True)

    stale_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = UploadEntry(
        id="job1",
        channel_id="ch1",
        status=STATUS_UPLOADING,
        title="My Video",
        extra={
            "upload_phase": "uploading",
            "upload_progress": 50,
            "upload_updated_at": stale_at,
            "upload_worker_id": "wrk_test",
        },
    )
    reg = UploadRegistry(reg_path)
    reg.append(entry)

    monkeypatch.setattr("uploader.upload_reconcile.reconcile_stale_seconds", lambda: 60)
    monkeypatch.setattr("uploader.upload_reconcile.reconcile_fail_seconds", lambda: 99999)
    monkeypatch.setattr("uploader.upload_reconcile.lock_is_expired_or_missing", lambda *a, **k: True)
    monkeypatch.setattr(
        "uploader.upload_reconcile.detect_storage_folder",
        lambda *a, **k: "queue",
    )
    monkeypatch.setattr(
        "uploader.upload_reconcile.list_channel_videos",
        lambda *a, **k: [],
    )

    result = reconcile_uploads(_config(ch), base=tmp_path, oauth=_oauth(), dry_run=False)
    assert any(a.action == "reset_pending" for a in result.actions)
    restored = reg.get("job1")
    assert restored is not None
    assert restored.status == "pending"


def test_reconcile_archives_uploaded_still_in_queue(tmp_path: Path, monkeypatch) -> None:
    ch = _channel(tmp_path)
    ch.registry_path = str(tmp_path / "registry.txt")
    reg_path = Path(ch.registry_path)
    reg_path.parent.mkdir(parents=True, exist_ok=True)

    entry = UploadEntry(
        id="job2",
        channel_id="ch1",
        status=STATUS_UPLOADED,
        youtube_id="yt123",
        video_uri=str(tmp_path / "queue/ch1/job2/video.mp4"),
    )
    reg = UploadRegistry(reg_path)
    reg.append(entry)

    archived: list[str] = []

    def _archive(entry, *, base, registry=None):
        archived.append(entry.id)
        return ["moved"]

    monkeypatch.setattr("uploader.upload_reconcile.detect_storage_folder", lambda *a, **k: "queue")
    monkeypatch.setattr("uploader.upload_reconcile.archive_job_from_entry", _archive)

    result = reconcile_uploads(_config(ch), base=tmp_path, oauth=_oauth(), dry_run=False)
    assert archived == ["job2"]
    assert any(a.action == "archived" for a in result.actions)
