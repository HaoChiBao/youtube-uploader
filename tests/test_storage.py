"""Tests for storage URI resolution and description loading."""

from pathlib import Path

import pytest

from uploader.storage import load_description, resolve_to_local_path


def test_resolve_local_path(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake video")
    result = resolve_to_local_path(str(video), temp_dir=tmp_path / "tmp")
    assert result == video.resolve()


def test_resolve_file_uri(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake video")
    uri = f"file://{video.resolve()}"
    result = resolve_to_local_path(uri, temp_dir=tmp_path / "tmp")
    assert result == video.resolve()


def test_resolve_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_to_local_path(str(tmp_path / "missing.mp4"), temp_dir=tmp_path)


def test_load_description_inline() -> None:
    assert load_description("Hello world") == "Hello world"


def test_load_description_from_file(tmp_path: Path) -> None:
    desc_file = tmp_path / "description.txt"
    desc_file.write_text("Chapter 1\n00:00 Intro\n", encoding="utf-8")
    assert load_description(str(desc_file)) == "Chapter 1\n00:00 Intro"


def test_load_description_file_uri(tmp_path: Path) -> None:
    desc_file = tmp_path / "description.txt"
    desc_file.write_text("From file URI", encoding="utf-8")
    uri = f"file://{desc_file.resolve()}"
    assert load_description(uri) == "From file URI"


def test_resolve_s3_without_boto3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import uploader.object_storage as object_storage

    monkeypatch.setattr(object_storage, "_default_s3_client", None)
    monkeypatch.setattr(object_storage, "_assembly_s3_client", None)

    def no_boto3(*_args, **_kwargs):
        raise RuntimeError("S3 support requires boto3. Install with: pip install '.[s3]'")

    monkeypatch.setattr(object_storage, "_build_s3_client", no_boto3)
    with pytest.raises(RuntimeError, match="boto3"):
        resolve_to_local_path("s3://bucket/key.mp4", temp_dir=tmp_path)
