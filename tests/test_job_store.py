"""Tests for staging jobs into channel queue storage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uploader import bucket_layout
from uploader.channels import ChannelConfig
from uploader.job_store import archive_job, generate_job_id, prepare_job_for_upload, remove_job, requeue_job, stage_job
from uploader.registry import STATUS_FAILED, STATUS_PENDING, STATUS_UPLOADED, UploadRegistry


@pytest.fixture
def channel(tmp_path: Path) -> ChannelConfig:
    return ChannelConfig(
        id="justcavefire",
        name="Cavefire",
        registry_path=str(tmp_path / "state" / "justcavefire" / "upload_registry.txt"),
        token_path=str(tmp_path / "secrets" / "justcavefire" / "youtube_token.json"),
    )


def test_stage_job_local(tmp_path: Path, channel: ChannelConfig, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video")
    thumb = tmp_path / "thumb.png"
    thumb.write_bytes(b"fake-png")

    staged = stage_job(
        channel,
        video_path=video,
        title="My Title",
        description="My description",
        thumbnail_path=thumb,
        job_id="test-job-01",
        base=tmp_path,
    )

    assert staged.job_id == "test-job-01"
    assert staged.video_uri.replace("\\", "/").endswith("queue/justcavefire/test-job-01/video.mp4")
    queue_dir = tmp_path / "queue" / "justcavefire" / "test-job-01"
    assert (queue_dir / "video.mp4").is_file()
    assert (queue_dir / "thumbnail.png").is_file()
    assert (queue_dir / "title.txt").read_text(encoding="utf-8") == "My Title\n"
    assert (queue_dir / "description.txt").read_text(encoding="utf-8") == "My description\n"
    manifest = json.loads((queue_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["title"] == "My Title"
    meta = json.loads((queue_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["privacy"] == "private"
    assert meta["is_short"] is False

    registry = UploadRegistry(channel.registry_path)
    pending = registry.pending(channel_id=channel.id)
    assert len(pending) == 1
    assert pending[0].id == "test-job-01"
    assert pending[0].video_uri == staged.video_uri


def test_archive_job_moves_to_uploaded(tmp_path: Path, channel: ChannelConfig, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video")

    stage_job(
        channel,
        video_path=video,
        title="T",
        description="D",
        job_id="job-archive",
        base=tmp_path,
    )

    moved = archive_job(channel.id, "job-archive", base=tmp_path)
    assert moved
    assert not (tmp_path / "queue" / "justcavefire" / "job-archive").exists()
    uploaded = tmp_path / "uploaded" / "justcavefire" / "job-archive"
    assert (uploaded / "video.mp4").is_file()


def test_generate_job_id() -> None:
    job_id = generate_job_id("justcavefire")
    assert job_id.startswith("justcavefire_")


def test_default_job_uris_use_queue_prefix(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", "my-bucket")
    uris = bucket_layout.default_job_uris("justcavefire", "job-001", tmp_path)
    assert "/queue/justcavefire/job-001/video.mp4" in uris["video_uri"]
    assert "/uploaded/justcavefire/job-001/" in uris["uploaded_prefix"]


def test_remove_job_deletes_queue_and_registry(tmp_path: Path, channel: ChannelConfig, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video")

    stage_job(
        channel,
        video_path=video,
        title="T",
        description="D",
        job_id="job-remove",
        base=tmp_path,
    )

    removed = remove_job(channel, "job-remove", base=tmp_path)
    assert removed.job_id == "job-remove"
    assert not (tmp_path / "queue" / "justcavefire" / "job-remove").exists()

    registry = UploadRegistry(channel.registry_path)
    assert registry.get("job-remove") is None


def test_remove_job_unknown_raises(tmp_path: Path, channel: ChannelConfig, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    with pytest.raises(ValueError, match="not found"):
        remove_job(channel, "missing-job", base=tmp_path)


def test_prepare_job_for_upload_retries_failed(tmp_path: Path, channel: ChannelConfig, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video")
    stage_job(
        channel,
        video_path=video,
        title="Retry me",
        description="",
        job_id="job-failed",
        base=tmp_path,
    )
    registry = UploadRegistry(channel.registry_path)
    registry.mark_failed("job-failed", error="timeout")

    restored = prepare_job_for_upload(channel, "job-failed", base=tmp_path, registry=registry)
    assert restored.status == STATUS_PENDING
    assert restored.error == ""
    assert registry.get("job-failed").status == STATUS_PENDING


def test_prepare_job_for_upload_requeues_uploaded(tmp_path: Path, channel: ChannelConfig, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video")
    stage_job(
        channel,
        video_path=video,
        title="Re-upload me",
        description="",
        job_id="job-done",
        base=tmp_path,
    )
    archive_job(channel.id, "job-done", base=tmp_path)
    registry = UploadRegistry(channel.registry_path)
    registry.mark_uploaded("job-done", youtube_id="abc123", publish_at="2026-07-02T12:00:00Z")

    restored = prepare_job_for_upload(channel, "job-done", base=tmp_path, registry=registry)
    assert restored.status == STATUS_PENDING
    assert restored.youtube_id == ""
    assert restored.youtube_url == ""
    assert (tmp_path / "queue" / "justcavefire" / "job-done" / "video.mp4").is_file()
    assert not (tmp_path / "uploaded" / "justcavefire" / "job-done").exists()


def test_requeue_job_moves_uploaded_prefix_back_to_queue(
    tmp_path: Path, channel: ChannelConfig, monkeypatch
) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video")
    stage_job(
        channel,
        video_path=video,
        title="Move back",
        description="",
        job_id="job-move",
        base=tmp_path,
    )
    archive_job(channel.id, "job-move", base=tmp_path)
    registry = UploadRegistry(channel.registry_path)
    registry.mark_uploaded("job-move", youtube_id="yt999")

    entry = requeue_job(channel.id, "job-move", base=tmp_path, registry=registry)
    assert entry.status == STATUS_PENDING
    assert registry.get("job-move").youtube_id == ""
