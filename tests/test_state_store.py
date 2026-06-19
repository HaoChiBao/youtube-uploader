"""Tests for R2-backed state persistence."""

from __future__ import annotations

from pathlib import Path

import yaml

from uploader.state_store import read_raw_config, write_raw_config


def test_read_raw_config_local(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    config_path = tmp_path / "config" / "channels.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("channels: []\n", encoding="utf-8")
    data = read_raw_config(config_path)
    assert data["channels"] == []


def test_write_raw_config_local(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    config_path = tmp_path / "config" / "channels.yaml"
    write_raw_config(config_path, {"channels": [{"id": "test"}], "google": {}})
    assert config_path.is_file()
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert loaded["channels"][0]["id"] == "test"


def test_sync_config_from_storage_adds_missing_channel(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    from uploader.state_store import init_channel_storage, read_raw_config, sync_config_from_storage

    config_path = tmp_path / "config" / "channels.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("channels: []\n\ngoogle:\n  oauth_port: 8080\n", encoding="utf-8")

    init_channel_storage(
        "yourdopaminerat",
        base=tmp_path,
        name="Dopamine Rat",
        youtube_channel_id="UC9fwC1kxFt_PhRNnX09Y0Fw",
        custom_url="@yourdopaminerat",
    )

    data = {"channels": [], "google": {"oauth_port": 8080}}
    assert sync_config_from_storage(data, tmp_path) is True
    assert len(data["channels"]) == 1
    assert data["channels"][0]["id"] == "yourdopaminerat"

    loaded = read_raw_config(config_path)
    assert any(ch["id"] == "yourdopaminerat" for ch in loaded["channels"])


def test_init_channel_storage_local(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    monkeypatch.delenv("UPLOADER_STORAGE_BUCKET", raising=False)
    from uploader.state_store import init_channel_storage

    init_channel_storage(
        "justcavefire",
        base=tmp_path,
        name="Cavefire",
        youtube_channel_id="UCabc123",
        custom_url="@justcavefire",
    )
    meta = tmp_path / "state" / "justcavefire" / "channel.meta.json"
    registry = tmp_path / "state" / "justcavefire" / "upload_registry.txt"
    assert meta.is_file()
    assert registry.is_file()
    assert (tmp_path / "secrets" / "justcavefire").is_dir()
    assert (tmp_path / "queue" / "justcavefire").is_dir()
    assert (tmp_path / "uploaded" / "justcavefire").is_dir()
    assert (tmp_path / "logs" / "justcavefire").is_dir()
    assert "justcavefire" in meta.read_text(encoding="utf-8")


def test_list_keys_returns_empty_when_r2_prefix_missing(monkeypatch) -> None:
    from botocore.exceptions import ClientError

    from uploader.object_storage import list_keys

    class FakeClient:
        def list_objects_v2(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
                "ListObjectsV2",
            )

    monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", "test-bucket")
    monkeypatch.setattr("uploader.object_storage._s3_client", lambda: FakeClient())
    assert list_keys("s3://test-bucket/state/") == []
