"""Default title and description for test uploads."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def default_test_title(*, channel_id: str, channel_name: str = "") -> str:
    """Generate a unique private test title (datetime-based)."""
    label = channel_name or channel_id
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"Test upload — {label} — {stamp}"


def default_test_description(
    *,
    channel_id: str,
    channel_name: str = "",
    timezone_name: str = "America/New_York",
) -> str:
    """Generate a placeholder description for smoke-test uploads."""
    label = channel_name or channel_id
    try:
        tz = ZoneInfo(timezone_name)
        when = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        when = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    return (
        f"Automated test upload for {label} ({channel_id}).\n"
        f"Uploaded at {when}.\n\n"
        "This video was uploaded via youtube-uploader for testing."
    )
