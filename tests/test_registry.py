"""Tests for upload registry."""

from pathlib import Path

from uploader.registry import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_UPLOADED,
    STATUS_UPLOADING,
    UploadEntry,
    UploadRegistry,
)


def test_append_and_pending(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.txt"
    registry = UploadRegistry(reg_path)

    entry = UploadEntry(
        id="job_01",
        channel_id="channel-a",
        title="Test Video",
        video_uri="./test.mp4",
    )
    registry.append(entry)

    pending = registry.pending()
    assert len(pending) == 1
    assert pending[0].id == "job_01"
    assert pending[0].status == STATUS_PENDING
    assert pending[0].created_at != ""


def test_pending_filter_by_channel(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.txt"
    registry = UploadRegistry(reg_path)
    registry.append(UploadEntry(id="a1", channel_id="channel-a"))
    registry.append(UploadEntry(id="b1", channel_id="channel-b"))

    assert len(registry.pending(channel_id="channel-a")) == 1
    assert registry.pending(channel_id="channel-a")[0].id == "a1"


def test_mark_uploading_uploaded_failed(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.txt"
    registry = UploadRegistry(reg_path)
    registry.append(UploadEntry(id="job_01", channel_id="channel-a"))

    registry.mark_uploading("job_01")
    entries = registry.load()
    assert entries[0].status == STATUS_UPLOADING

    registry.mark_uploaded("job_01", youtube_id="abc123", publish_at="2026-06-20T13:00:00Z")
    entries = registry.load()
    assert entries[0].status == STATUS_UPLOADED
    assert entries[0].youtube_id == "abc123"
    assert entries[0].youtube_url == "https://youtu.be/abc123"
    assert entries[0].publish_at == "2026-06-20T13:00:00Z"
    assert entries[0].uploaded_at != ""

    registry.append(UploadEntry(id="job_02", channel_id="channel-a"))
    registry.mark_failed("job_02", error="timeout")
    entries = registry.load()
    failed = [e for e in entries if e.id == "job_02"][0]
    assert failed.status == STATUS_FAILED
    assert failed.error == "timeout"


def test_legacy_video_field(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.txt"
    registry = UploadRegistry(reg_path)
    entry = UploadEntry(
        id="legacy_01",
        channel_id="channel-a",
        video="/path/to/video.mp4",
        thumbnail="/path/to/thumb.png",
    )
    registry.append(entry)
    loaded = registry.load()[0]
    assert loaded.resolved_video_uri() == "/path/to/video.mp4"
    assert loaded.resolved_thumbnail_uri() == "/path/to/thumb.png"


def test_extra_fields_preserved(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.txt"
    registry = UploadRegistry(reg_path)
    raw = (
        '{"id": "x1", "channel_id": "a", "custom_field": "value", '
        '"extra": {"nested": true}}'
    )
    reg_path.write_text(raw + "\n", encoding="utf-8")
    entry = registry.load()[0]
    assert entry.extra.get("custom_field") == "value"
    assert entry.extra.get("nested") is True
