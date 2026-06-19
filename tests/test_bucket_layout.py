"""Tests for canonical bucket layout paths."""

from __future__ import annotations

from pathlib import Path

from uploader import bucket_layout


def test_job_key_and_uris(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", "my-bucket")
    base = tmp_path
    assert bucket_layout.job_key("justcavefire", "job-001", bucket_layout.JOB_VIDEO) == (
        "queue/justcavefire/job-001/video.mp4"
    )
    uris = bucket_layout.default_job_uris("justcavefire", "job-001", base)
    assert uris["video_uri"] == "s3://my-bucket/queue/justcavefire/job-001/video.mp4"
    assert uris["thumbnail_uri"].endswith("/thumbnail.png")


def test_local_paths_without_bucket(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    monkeypatch.delenv("UPLOADER_STORAGE_BUCKET", raising=False)
    base = tmp_path
    token = bucket_layout.token_location("foo", base)
    assert token.replace("\\", "/").endswith("secrets/foo/youtube_token.json")
    assert not token.startswith("s3://")


def test_is_default_refs() -> None:
    assert bucket_layout.is_default_token_ref("secrets/foo/youtube_token.json", "foo")
    assert bucket_layout.is_default_registry_ref("state/foo/upload_registry.txt", "foo")
