"""In-memory OAuth session store (local dev)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

_PENDING: dict[str, "OAuthSession"] = {}
_TTL_SEC = 600


@dataclass
class OAuthSession:
    nonce: str
    mode: Literal["add", "reauth"]
    channel_id: str = ""
    code_verifier: str = ""
    created_at: float = 0.0


def save_session(session: OAuthSession) -> None:
    session.created_at = time.time()
    _PENDING[session.nonce] = session


def pop_session(nonce: str) -> OAuthSession | None:
    _purge_expired()
    session = _PENDING.pop(nonce, None)
    return session


def _purge_expired() -> None:
    now = time.time()
    expired = [k for k, v in _PENDING.items() if now - v.created_at > _TTL_SEC]
    for k in expired:
        del _PENDING[k]
