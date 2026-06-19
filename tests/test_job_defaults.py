"""Tests for layered job metadata defaults."""

from __future__ import annotations

import os

import pytest

from uploader.channels import ChannelConfig
from uploader.job_defaults import (
    DEFAULT_PRIVACY,
    JobDefaults,
    defaults_for_channel,
    global_job_defaults,
    merge_job_defaults,
)


def test_global_job_defaults_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPLOADER_DEFAULT_PRIVACY", "unlisted")
    monkeypatch.setenv("UPLOADER_DEFAULT_IS_SHORT", "true")
    monkeypatch.setenv("UPLOADER_DEFAULT_TAGS", "lofi,chill")
    d = global_job_defaults()
    assert d.privacy == "unlisted"
    assert d.is_short is True
    assert d.tags == ["lofi", "chill"]


def test_defaults_for_channel_overrides_global(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UPLOADER_DEFAULT_PRIVACY", raising=False)
    channel = ChannelConfig(
        id="justcavefire",
        default_privacy="public",
        default_is_short=True,
        category_id="10",
        default_tags=["music"],
    )
    d = defaults_for_channel(channel, JobDefaults(privacy="private"))
    assert d.privacy == "public"
    assert d.is_short is True
    assert d.tags == ["music"]


def test_cli_override_wins() -> None:
    channel = ChannelConfig(id="c", default_privacy="public")
    d = defaults_for_channel(
        channel,
        JobDefaults(),
        override_privacy="private",
        override_is_short=True,
    )
    assert d.privacy == "private"
    assert d.is_short is True


def test_overlay_from_dict_partial() -> None:
    base = JobDefaults(privacy="private", is_short=False, language="en")
    merged = JobDefaults.overlay_from_dict(base, {"privacy": "public"})
    assert merged.privacy == "public"
    assert merged.is_short is False
    assert merged.language == "en"
