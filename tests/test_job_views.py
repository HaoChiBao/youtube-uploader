"""Tests for unified job listing (queue vs uploaded)."""

from __future__ import annotations

from pathlib import Path

from uploader.channels import ChannelConfig
from uploader.job_store import archive_job, stage_job
from uploader.job_views import detect_storage_folder, load_channel_jobs
from uploader.registry import STATUS_UPLOADED, UploadRegistry


def _channel(tmp_path: Path, channel_id: str = "testchan") -> ChannelConfig:
    return ChannelConfig(
        id=channel_id,
        name="Test",
        registry_path=str(tmp_path / "state" / channel_id / "upload_registry.txt"),
    )


def test_load_channel_jobs_queue_fifo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    ch = _channel(tmp_path)
    stage_job(ch, video_path=video, title="First", description="", job_id="job-one", base=tmp_path)
    stage_job(ch, video_path=video, title="Second", description="", job_id="job-two", base=tmp_path)

    bundle = load_channel_jobs(ch, base=tmp_path)
    assert bundle.pending_count == 2
    assert len(bundle.queue_jobs) == 2
    assert bundle.queue_jobs[0].queue_position == 1
    assert bundle.queue_jobs[1].queue_position == 2
    assert bundle.queue_jobs[0].storage_folder == "queue"
    assert bundle.uploaded_count == 0


def test_archive_moves_to_uploaded_folder(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    ch = _channel(tmp_path)
    staged = stage_job(ch, video_path=video, title="Vid", description="", base=tmp_path)

    reg = UploadRegistry(ch.registry_path)
    reg.mark_uploaded(staged.job_id, youtube_id="abc123")
    archive_job(ch.id, staged.job_id, base=tmp_path)
    reg.update_storage_uris(
        staged.job_id,
        video_uri=str(tmp_path / "uploaded" / ch.id / staged.job_id / "video.mp4"),
    )

    assert detect_storage_folder(ch.id, staged.job_id, base=tmp_path, status=STATUS_UPLOADED) == "uploaded"

    bundle = load_channel_jobs(ch, base=tmp_path)
    assert bundle.pending_count == 0
    assert bundle.uploaded_count == 1
    assert bundle.uploaded_jobs[0].storage_folder == "uploaded"
    assert bundle.uploaded_jobs[0].youtube_id == "abc123"


def test_detect_storage_folder_from_video_uri_when_object_missing(tmp_path: Path, monkeypatch) -> None:
    """Pending jobs should show queue/ from registry URI even if only nested prefix exists."""
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    ch = _channel(tmp_path)
    nested = tmp_path / "youtuber-uploader" / "queue" / ch.id / "legacy-job"
    nested.mkdir(parents=True)
    (nested / "video.mp4").write_bytes(b"x")

    video_uri = str(tmp_path / "queue" / ch.id / "legacy-job" / "video.mp4")
    folder = detect_storage_folder(
        ch.id,
        "legacy-job",
        base=tmp_path,
        status="pending",
        video_uri=video_uri,
    )
    assert folder == "queue"
