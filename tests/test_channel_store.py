"""Tests for dynamic channel registration."""

from __future__ import annotations

from pathlib import Path

import yaml

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


def test_register_oauth_same_account_keeps_channel_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    from uploader.channel_info import AuthorizedChannelInfo
    from uploader.channel_store import register_oauth_channel
    from uploader.state_store import write_raw_config

    config_path = tmp_path / "config" / "channels.yaml"
    write_raw_config(
        config_path,
        {
            "channels": [
                {
                    "id": "justcavefire",
                    "name": "Cavefire",
                    "youtube_channel_id": "UC_OLD",
                    "custom_url": "@justcavefire",
                    "token_path": str(tmp_path / "secrets/justcavefire/youtube_token.json"),
                    "registry_path": str(tmp_path / "state/justcavefire/upload_registry.txt"),
                    "category_id": "22",
                    "default_tags": ["gaming"],
                    "made_for_kids": False,
                    "publish": {"timezone": "UTC", "hour": 12, "interval_hours": 48.0},
                }
            ],
            "google": {"oauth_port": 8080},
        },
    )

    info = AuthorizedChannelInfo(
        youtube_channel_id="UC_OLD",
        title="Cavefire Updated",
        custom_url="@justcavefire",
    )
    result = register_oauth_channel(
        '{"token": "fresh"}',
        config_path=config_path,
        reauth_channel_id="justcavefire",
        info=info,
    )

    assert result.action == "updated"
    assert result.channel.id == "justcavefire"
    assert result.channel.name == "Cavefire Updated"
    assert result.channel.category_id == "22"
    assert result.channel.default_tags == ["gaming"]
    assert result.channel.publish.timezone == "UTC"
    assert result.channel.publish.hour == 12

    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert len(loaded["channels"]) == 1
    assert loaded["channels"][0]["id"] == "justcavefire"
    assert loaded["channels"][0]["youtube_channel_id"] == "UC_OLD"
    token = tmp_path / "secrets" / "justcavefire" / "youtube_token.json"
    assert token.is_file()
    assert token.read_text(encoding="utf-8") == '{"token": "fresh"}'


def test_register_oauth_different_account_adds_new_channel(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    from uploader.channel_info import AuthorizedChannelInfo
    from uploader.channel_store import register_oauth_channel
    from uploader.state_store import write_raw_config

    config_path = tmp_path / "config" / "channels.yaml"
    old_token = tmp_path / "secrets" / "justcavefire" / "youtube_token.json"
    old_token.parent.mkdir(parents=True)
    old_token.write_text('{"token": "keep-me"}', encoding="utf-8")

    write_raw_config(
        config_path,
        {
            "channels": [
                {
                    "id": "justcavefire",
                    "name": "Cavefire",
                    "youtube_channel_id": "UC_OLD",
                    "custom_url": "@justcavefire",
                    "token_path": str(old_token),
                    "registry_path": str(tmp_path / "state/justcavefire/upload_registry.txt"),
                    "publish": {"timezone": "America/New_York", "hour": 9, "interval_hours": 24.0},
                }
            ],
            "google": {"oauth_port": 8080},
        },
    )

    info = AuthorizedChannelInfo(
        youtube_channel_id="UC_NEW",
        title="Different Channel",
        custom_url="@different",
    )
    result = register_oauth_channel(
        '{"token": "new-account"}',
        config_path=config_path,
        reauth_channel_id="justcavefire",
        info=info,
    )

    assert result.action == "added"
    assert result.channel.id == "different"
    assert result.channel.youtube_channel_id == "UC_NEW"

    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert len(loaded["channels"]) == 2
    original = next(ch for ch in loaded["channels"] if ch["id"] == "justcavefire")
    assert original["youtube_channel_id"] == "UC_OLD"
    assert original["name"] == "Cavefire"
    assert old_token.read_text(encoding="utf-8") == '{"token": "keep-me"}'

    new_token = tmp_path / "secrets" / "different" / "youtube_token.json"
    assert new_token.is_file()
    assert new_token.read_text(encoding="utf-8") == '{"token": "new-account"}'


def test_resolve_registry_path_local_without_bucket(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    monkeypatch.delenv("UPLOADER_STORAGE_BUCKET", raising=False)
    base = tmp_path
    path = _resolve_registry_path("state/foo/upload_registry.txt", base, "foo")
    assert path.replace("\\", "/").endswith("state/foo/upload_registry.txt")
    assert not path.startswith("s3://")
