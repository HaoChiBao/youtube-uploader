"""Optional API key auth for machine-to-machine ingest."""

from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """When UPLOADER_API_KEY is set, require matching X-API-Key header."""
    expected = os.environ.get("UPLOADER_API_KEY", "").strip()
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(401, "Invalid or missing API key (X-API-Key header)")
