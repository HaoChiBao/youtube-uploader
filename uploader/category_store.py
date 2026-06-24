"""Persist assembly/content category labels in channels.yaml."""

from __future__ import annotations

from pathlib import Path

from uploader.state_store import read_raw_config, write_raw_config


def normalize_content_category(value: str) -> str:
    """Trim assembly/content category label (e.g. korean), separate from YouTube category_id."""
    return value.strip()


class CategoryError(ValueError):
    """Invalid category name or duplicate."""


class CategoryNotFoundError(KeyError):
    """Category does not exist in the saved list."""


def _category_key(name: str) -> str:
    return normalize_content_category(name).lower()


def _has_category(categories: list[str], name: str) -> bool:
    key = _category_key(name)
    if not key:
        return False
    return any(_category_key(c) == key for c in categories)


def _dedupe_categories(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        name = normalize_content_category(str(raw))
        if not name:
            continue
        key = _category_key(name)
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def categories_from_data(data: dict) -> list[str]:
    """Return unique saved categories in stable order."""
    stored = _dedupe_categories(list(data.get("categories") or []))
    assigned = [
        normalize_content_category(str(raw.get("category") or ""))
        for raw in data.get("channels") or []
    ]
    return _dedupe_categories(stored + [c for c in assigned if c])


def merge_channel_categories(data: dict) -> bool:
    """Ensure categories list includes labels assigned on channels. Returns True if changed."""
    merged = categories_from_data(data)
    current = _dedupe_categories(list(data.get("categories") or []))
    if merged == current:
        return False
    data["categories"] = merged
    return True


def list_saved_categories(config_path: Path, *, sync: bool = False) -> list[str]:
    config_path = config_path.expanduser().resolve()
    data = read_raw_config(config_path, sync=sync, migrate=False)
    if merge_channel_categories(data):
        write_raw_config(config_path, data)
    return categories_from_data(data)


def add_category(name: str, *, config_path: Path) -> list[str]:
    config_path = config_path.expanduser().resolve()
    name = normalize_content_category(name)
    if not name:
        raise CategoryError("Category name is required")

    data = read_raw_config(config_path, sync=True, migrate=True)
    merge_channel_categories(data)
    categories = _dedupe_categories(list(data.get("categories") or []))
    if _has_category(categories, name):
        raise CategoryError(f"Category already exists: {name}")

    categories.append(name)
    data["categories"] = categories
    write_raw_config(config_path, data)
    return categories


def remove_category(name: str, *, config_path: Path) -> list[str]:
    config_path = config_path.expanduser().resolve()
    name = normalize_content_category(name)
    if not name:
        raise CategoryError("Category name is required")

    data = read_raw_config(config_path, sync=True, migrate=True)
    merge_channel_categories(data)
    categories = _dedupe_categories(list(data.get("categories") or []))
    if not _has_category(categories, name):
        raise CategoryNotFoundError(f"Category not found: {name}")

    remove_key = _category_key(name)
    data["categories"] = [c for c in categories if _category_key(c) != remove_key]

    for raw in data.get("channels") or []:
        assigned = normalize_content_category(str(raw.get("category") or ""))
        if assigned and _category_key(assigned) == remove_key:
            raw.pop("category", None)

    write_raw_config(config_path, data)
    return list(data["categories"])


def validate_channel_category(category: str, data: dict) -> None:
    """Raise CategoryNotFoundError when category is set but not in the saved list."""
    category = normalize_content_category(category)
    if not category:
        return
    merge_channel_categories(data)
    if not _has_category(categories_from_data(data), category):
        raise CategoryNotFoundError(
            f"Unknown category {category!r}. Create it under Categories first."
        )
