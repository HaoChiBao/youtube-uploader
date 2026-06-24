"""Tests for saved assembly/content categories."""

from __future__ import annotations

from pathlib import Path

import yaml

from uploader.category_store import (
    CategoryError,
    CategoryNotFoundError,
    add_category,
    categories_from_data,
    list_saved_categories,
    remove_category,
)


def test_categories_from_data_dedupes_case_insensitive() -> None:
    data = {
        "categories": ["Korean", "korean", "japanese"],
        "channels": [{"id": "a", "category": "Korean"}],
    }
    assert categories_from_data(data) == ["Korean", "japanese"]


def test_add_category_rejects_duplicate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    config_path = tmp_path / "config" / "channels.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("channels: []\ncategories: [korean]\n", encoding="utf-8")

    try:
        add_category("Korean", config_path=config_path)
        assert False, "expected duplicate error"
    except CategoryError:
        pass


def test_remove_category_clears_channels(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    config_path = tmp_path / "config" / "channels.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        yaml.dump(
            {
                "categories": ["korean", "japanese"],
                "channels": [
                    {"id": "a", "category": "korean"},
                    {"id": "b", "category": "japanese"},
                ],
            }
        ),
        encoding="utf-8",
    )

    categories = remove_category("korean", config_path=config_path)
    assert "korean" not in categories
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert loaded["channels"][0].get("category") is None
    assert loaded["channels"][1]["category"] == "japanese"


def test_remove_missing_category(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    config_path = tmp_path / "config" / "channels.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("channels: []\ncategories: []\n", encoding="utf-8")

    try:
        remove_category("nope", config_path=config_path)
        assert False, "expected not found"
    except CategoryNotFoundError:
        pass


def test_list_saved_categories_merges_channel_labels(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_R2_BUCKET", raising=False)
    config_path = tmp_path / "config" / "channels.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        yaml.dump({"categories": [], "channels": [{"id": "a", "category": "retro"}]}),
        encoding="utf-8",
    )

    categories = list_saved_categories(config_path)
    assert categories == ["retro"]
