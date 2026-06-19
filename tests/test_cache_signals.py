"""Tests for API cache invalidation."""

from __future__ import annotations

from uploader.cache_signals import bump, generation


def test_bump_increments_generation() -> None:
    before = generation("queue")
    bump("queue")
    assert generation("queue") == before + 1


def test_bump_all() -> None:
    cfg, queue, tok = generation("config"), generation("queue"), generation("tokens")
    bump("all")
    assert generation("config") == cfg + 1
    assert generation("queue") == queue + 1
    assert generation("tokens") == tok + 1
