"""Tests for dynamic channel registration."""

from __future__ import annotations

from pathlib import Path

from uploader.channel_info import AuthorizedChannelInfo
from uploader.channel_store import (
    derive_channel_id,
    make_unique_channel_id,
    slugify,
    _read_raw_config,
    find_channel_index,
)
from uploader.channels import resolve_channel, ChannelConfig, AppConfig, PublishConfig, GoogleConfig, _resolve_registry_path


def test_slugify_handle():
    assert slugify("@MyCoolChannel") == "mycoolchannel"
    assert slugify("Lofi Beats 24/7") == "lofi-beats-24-7"


def test_derive_channel_id_prefers_handle():
    info = AuthorizedChannelInfo(
        youtube_channel_id="UC123",
        title="My Title",
        custom_url="@myhandle",
    )
    assert derive_channel_id(info) == "myhandle"


def test_derive_channel_id_falls_back_to_title():
    info = AuthorizedChannelInfo(
        youtube_channel_id="UC123",
        title="Lofi Radio",
        custom_url="",
    )
    assert derive_channel_id(info) == "lofi-radio"


def test_make_unique_channel_id_collision():
    existing = {"lofi-radio": "UCother"}
    assert make_unique_channel_id("lofi-radio", "UC123456", existing) == "lofi-radio-123456"


def test_make_unique_channel_id_same_channel_reauth():
    existing = {"lofi-radio": "UC123456"}
    assert make_unique_channel_id("lofi-radio", "UC123456", existing) == "lofi-radio"


def test_find_channel_index():
    data = {"channels": [{"youtube_channel_id": "UCabc"}]}
    assert find_channel_index(data, "UCabc") == 0
    assert find_channel_index(data, "UCmissing") is None


def test_resolve_channel_by_name():
    config = AppConfig(
        channels=[
            ChannelConfig(
                id="lofi-radio",
                name="Lofi Radio",
                youtube_channel_id="UC123",
                custom_url="@lofiradio",
            )
        ],
        google=GoogleConfig(),
    )
    assert resolve_channel(config, "lofi-radio").id == "lofi-radio"
    assert resolve_channel(config, "Lofi Radio").id == "lofi-radio"
    assert resolve_channel(config, "@lofiradio").id == "lofi-radio"
    assert resolve_channel(config, "UC123").id == "lofi-radio"


def test_read_raw_config_creates_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    monkeypatch.delenv("UPLOADER_STORAGE_BUCKET", raising=False)
    path = tmp_path / "config" / "channels.yaml"
    data = _read_raw_config(path)
    assert data["channels"] == []
    assert path.is_file()


def test_resolve_registry_path_uses_bucket(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", "my-bucket")
    monkeypatch.delenv("UPLOADER_STORAGE_BUCKET", raising=False)
    base = tmp_path
    assert _resolve_registry_path("", base, "justcavefire") == (
        "s3://my-bucket/state/justcavefire/upload_registry.txt"
    )
    assert _resolve_registry_path("state/justcavefire/upload_registry.txt", base, "justcavefire") == (
        "s3://my-bucket/state/justcavefire/upload_registry.txt"
    )


def test_resolve_registry_path_local_without_bucket(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    monkeypatch.delenv("UPLOADER_STORAGE_BUCKET", raising=False)
    base = tmp_path
    path = _resolve_registry_path("state/foo/upload_registry.txt", base, "foo")
    assert path.replace("\\", "/").endswith("state/foo/upload_registry.txt")
    assert not path.startswith("s3://")
