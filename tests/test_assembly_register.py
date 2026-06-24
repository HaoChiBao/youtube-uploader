"""Tests for ai-music-assembler register integration."""

from pathlib import Path

import pytest

from uploader.channels import ChannelConfig
from uploader.job_store import (
    _infer_job_id_from_video_uri,
    _should_reference_uri,
    register_job_from_uris,
)
from uploader.registry import UploadRegistry


def test_infer_job_id_from_assembly_uri() -> None:
    uri = (
        "s3://music-assembly-data/music-video/nappabeats/"
        "mv_20260624_061500/mv_20260624_061500_video.mp4"
    )
    assert _infer_job_id_from_video_uri(uri, "nappabeats") == "mv_20260624_061500"


def test_should_reference_uri_external_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", "youtuber-uploader")
    monkeypatch.setenv("ASSEMBLY_R2_BUCKET", "music-assembly-data")
    uri = "s3://music-assembly-data/music-video/nappabeats/mv_1/mv_1_video.mp4"
    assert _should_reference_uri(uri) is True


def test_should_reference_uri_uploader_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", "youtuber-uploader")
    uri = "s3://youtuber-uploader/queue/nappabeats/job1/video.mp4"
    assert _should_reference_uri(uri) is False


def test_register_idempotent_local(tmp_path: Path) -> None:
    channel = ChannelConfig(
        id="testchan",
        name="Test",
        token_path="secrets/testchan/token.json",
        registry_path=str(tmp_path / "registry.txt"),
    )
    job_id = "mv_test_1"
    video = tmp_path / "external" / f"{job_id}_video.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video-bytes")

    staged1, created1 = register_job_from_uris(
        channel,
        title="Title",
        description="Desc",
        video_uri=str(video),
        job_id=job_id,
        base=tmp_path,
    )
    assert created1 is True
    assert staged1.job_id == job_id

    staged2, created2 = register_job_from_uris(
        channel,
        title="Title",
        description="Desc",
        video_uri=str(video),
        job_id=job_id,
        base=tmp_path,
    )
    assert created2 is False
    assert staged2.job_id == job_id

    reg = UploadRegistry(channel.registry_path)
    pending = reg.pending(channel_id=channel.id)
    assert len(pending) == 1


def test_register_duplicate_job_id_conflict(tmp_path: Path) -> None:
    channel = ChannelConfig(
        id="testchan",
        name="Test",
        token_path="secrets/testchan/token.json",
        registry_path=str(tmp_path / "registry.txt"),
    )
    v1 = tmp_path / "a.mp4"
    v2 = tmp_path / "b.mp4"
    v1.write_bytes(b"a")
    v2.write_bytes(b"b")

    register_job_from_uris(
        channel,
        title="A",
        description="",
        video_uri=str(v1),
        job_id="same-id",
        base=tmp_path,
    )

    with pytest.raises(ValueError, match="different video_uri"):
        register_job_from_uris(
            channel,
            title="B",
            description="",
            video_uri=str(v2),
            job_id="same-id",
            base=tmp_path,
        )

