"""Tests for job metadata files in queue folders."""

from __future__ import annotations

import json
from pathlib import Path

from uploader.channels import ChannelConfig
from uploader.job_metadata import JobMetadata, load_job_metadata, write_job_metadata_files
from uploader.job_store import stage_job
from uploader.registry import UploadEntry, UploadRegistry


def test_stage_job_writes_full_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    channel = ChannelConfig(
        id="justcavefire",
        name="Cavefire",
        registry_path=str(tmp_path / "state" / "justcavefire" / "upload_registry.txt"),
        default_tags=["lofi"],
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")

    staged = stage_job(
        channel,
        video_path=video,
        title="Short clip",
        description="Desc",
        job_id="job-meta",
        base=tmp_path,
        privacy="public",
        is_short=True,
        tags=["chill"],
        made_for_kids=False,
    )

    job_dir = tmp_path / "queue" / "justcavefire" / "job-meta"
    meta = json.loads((job_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["privacy"] == "public"
    assert meta["is_short"] is True
    assert meta["tags"] == ["chill"]
    assert (job_dir / "privacy.txt").read_text(encoding="utf-8").strip() == "public"
    assert (job_dir / "is_short.txt").read_text(encoding="utf-8").strip() == "true"
    assert staged.metadata.privacy == "public"


def test_load_job_metadata_from_folder(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    channel = ChannelConfig(
        id="justcavefire",
        registry_path=str(tmp_path / "state" / "justcavefire" / "upload_registry.txt"),
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    stage_job(
        channel,
        video_path=video,
        title="T",
        description="D",
        job_id="load-test",
        base=tmp_path,
        privacy="unlisted",
        is_short=False,
    )

    entry = UploadEntry(
        id="load-test",
        channel_id="justcavefire",
        video_uri=str(tmp_path / "queue" / "justcavefire" / "load-test" / "video.mp4"),
    )
    loaded = load_job_metadata(entry, base=tmp_path, channel=channel)
    assert loaded is not None
    assert loaded.privacy == "unlisted"
    assert loaded.title == "T"
    assert loaded.is_short is False


def test_effective_tags_adds_shorts() -> None:
    meta = JobMetadata(
        id="x",
        channel_id="c",
        title="t",
        description="d",
        is_short=True,
        tags=["music"],
    )
    assert "Shorts" in meta.effective_tags()
