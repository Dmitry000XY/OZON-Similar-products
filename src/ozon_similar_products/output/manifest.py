"""Helpers for recommendation output manifests."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

DEFAULT_MANIFEST_FILENAME = "manifest.json"

COMPACT_RECOMMENDATIONS_PATH_KEYS = (
    "widget_recommendations_path",
    "compact_recommendations_path",
    "similar_items_path",
    "widget_path",
    "lookup_recommendations_path",
    "lookup_path",
    "recommendations_path",
)

RECOMMENDATION_ARTIFACT_PATH_KEYS = (
    "detailed_recommendations_path",
    "enriched_recommendations_path",
    *COMPACT_RECOMMENDATIONS_PATH_KEYS,
)


def load_manifest(manifest_path: str | Path) -> dict[str, Any]:
    """Load a manifest JSON file as a dictionary."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise TypeError("manifest JSON must contain an object")
    return manifest


def find_manifest_path(manifest: Mapping[str, Any], *keys: str) -> str | None:
    """Find a path value by key in flat or nested ``paths`` manifest sections."""
    for key in keys:
        value = manifest.get(key)
        if isinstance(value, str):
            return value

    paths = manifest.get("paths")
    if isinstance(paths, Mapping):
        for key in keys:
            value = paths.get(key)
            if isinstance(value, str):
                return value

    return None


def find_compact_recommendations_path(manifest: Mapping[str, Any]) -> str | None:
    """Find compact recommendations path in flat or nested manifest data."""
    return find_manifest_path(manifest, *COMPACT_RECOMMENDATIONS_PATH_KEYS)


def json_ready(value: Any) -> Any:
    """Convert common Python objects to JSON-compatible values."""
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [json_ready(item) for item in value]
    return value


def rebase_manifest_paths(
    manifest: Mapping[str, Any],
    source_base: Path,
    target_base: Path,
) -> dict[str, Any]:
    """Rebase known artifact paths from one manifest location to another."""
    return {
        str(key): _rebase_value(
            value,
            key=str(key),
            source_base=source_base,
            target_base=target_base,
        )
        for key, value in manifest.items()
    }


def _rebase_value(
    value: Any,
    key: str,
    source_base: Path,
    target_base: Path,
) -> Any:
    """Recursively rebase known manifest path fields."""
    if isinstance(value, Mapping):
        return {
            str(child_key): _rebase_value(
                child_value,
                key=str(child_key),
                source_base=source_base,
                target_base=target_base,
            )
            for child_key, child_value in value.items()
        }

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [
            _rebase_value(
                item,
                key=key,
                source_base=source_base,
                target_base=target_base,
            )
            for item in value
        ]

    if isinstance(value, str) and key in RECOMMENDATION_ARTIFACT_PATH_KEYS:
        return _rebase_path_string(value, source_base=source_base, target_base=target_base)

    return json_ready(value)


def _rebase_path_string(path_value: str, source_base: Path, target_base: Path) -> str:
    """Return a path string that is valid relative to the target directory."""
    source_path = Path(path_value)
    absolute_path = source_path if source_path.is_absolute() else source_base / source_path
    relative_path = os.path.relpath(absolute_path.resolve(), start=target_base.resolve())
    return Path(relative_path).as_posix()
