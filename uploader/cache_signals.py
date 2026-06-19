"""Lightweight cache-invalidation generations (API + CLI shared).

Bump a generation when durable state changes so in-memory caches refresh.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_generations: dict[str, int] = {"config": 0, "queue": 0, "tokens": 0}


def bump(kind: str = "all") -> None:
    """Invalidate cached data. kind: config | queue | tokens | all."""
    with _lock:
        if kind == "all":
            for key in _generations:
                _generations[key] += 1
            return
        if kind in _generations:
            _generations[kind] += 1


def generation(kind: str) -> int:
    with _lock:
        return _generations.get(kind, 0)
