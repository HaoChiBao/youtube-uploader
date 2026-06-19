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
