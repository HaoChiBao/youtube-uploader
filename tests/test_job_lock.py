"""Tests for upload job locking."""

from __future__ import annotations

from pathlib import Path

from uploader.job_claim import try_claim_job
from uploader.job_lock import acquire_upload_lock, release_upload_lock
from uploader.registry import STATUS_PENDING, UploadEntry, UploadRegistry


def test_acquire_lock_local_exclusive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    assert acquire_upload_lock("ch1", "job_a", "worker_1", base=tmp_path)
    assert not acquire_upload_lock("ch1", "job_a", "worker_2", base=tmp_path)
    release_upload_lock("ch1", "job_a", base=tmp_path, worker_id="worker_1")
    assert acquire_upload_lock("ch1", "job_a", "worker_2", base=tmp_path)


def test_try_claim_job_pending(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    reg_path = tmp_path / "state" / "ch1" / "upload_registry.txt"
    reg_path.parent.mkdir(parents=True)
    registry = UploadRegistry(reg_path)
    registry.append(
        UploadEntry(id="job_1", channel_id="ch1", status=STATUS_PENDING, title="Test")
    )

    first = try_claim_job(registry, "ch1", "job_1", "worker_a", base=tmp_path)
    assert first.claimed
    second = try_claim_job(registry, "ch1", "job_1", "worker_b", base=tmp_path)
    assert not second.claimed

    release_upload_lock("ch1", "job_1", base=tmp_path, worker_id="worker_a")


def test_cancel_upload_job_returns_to_pending(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    from uploader.job_claim import cancel_upload_job

    reg_path = tmp_path / "state" / "ch1" / "upload_registry.txt"
    reg_path.parent.mkdir(parents=True)
    registry = UploadRegistry(reg_path)
    registry.append(
        UploadEntry(id="job_1", channel_id="ch1", status=STATUS_PENDING, title="Test")
    )

    claim = try_claim_job(registry, "ch1", "job_1", "worker_a", base=tmp_path)
    assert claim.claimed

    restored = cancel_upload_job(registry, "ch1", "job_1", base=tmp_path)
    assert restored.status == STATUS_PENDING
    assert restored.extra.get("upload_phase") is None

    reclaim = try_claim_job(registry, "ch1", "job_1", "worker_b", base=tmp_path)
    assert reclaim.claimed
    release_upload_lock("ch1", "job_1", base=tmp_path, worker_id="worker_b")
