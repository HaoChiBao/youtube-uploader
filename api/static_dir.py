"""Resolve dashboard static assets (index.html) for dev and container installs."""

from __future__ import annotations

import os
from pathlib import Path


def static_dir() -> Path:
    """Return the directory containing index.html and other dashboard assets."""
    override = os.environ.get("UPLOADER_STATIC_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent / "static"
