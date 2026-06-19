"""Tests for default upload metadata."""

from __future__ import annotations

from uploader.metadata import default_test_description, default_test_title


def test_default_test_title_includes_channel_and_timestamp():
    title = default_test_title(channel_id="channel-a", channel_name="My Channel")
    assert "channel-a" in title or "My Channel" in title
    assert "Test upload" in title


def test_default_test_description_includes_channel():
    desc = default_test_description(
        channel_id="channel-b",
        channel_name="Second",
        timezone_name="America/New_York",
    )
    assert "channel-b" in desc
    assert "Second" in desc
    assert "test upload" in desc.lower()
