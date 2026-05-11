"""Lookup helper for saved similar items recommendations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.validation import validate_widget_output

DEFAULT_LOOKUP_FILENAME = "similar_items.parquet"

_LOOKUP_PATH_KEYS = (
    "widget_recommendations_path",
    "compact_recommendations_path",
    "similar_items_path",
    "widget_path",
    "recommendations_path",
)


class SimilarItemsLookup:
    """Read saved compact recommendations and return similar items.

    The lookup layer does not rebuild recommendations. It only reads the compact
    output produced by RecommendationWriter, builds an in-memory mapping, and
    serves rank-ordered similar items for a requested item_id.
    """

    def __init__(self, recommendations_path: str | Path) -> None:
        self.recommendations_path = Path(recommendations_path)
        self.resolved_recommendations_path = _resolve_recommendations_path(
            self.recommendations_path
        )
        self.recommendations = pl.read_parquet(self.resolved_recommendations_path)
        validate_widget_output(self.recommendations)
        self._items_by_item_id = _build_lookup_mapping(self.recommendations)

    def get_similar_items(
        self,
        item_id: int | str,
        top_k: int = 10,
    ) -> list[int | str]:
        """Return top-K similar items for item_id.

        Unknown item_id values return an empty list. The method never recalculates
        recommendations and never reads raw events.
        """
        if top_k <= 0:
            raise ValueError("top_k must be a positive integer")

        similar_items = self._items_by_item_id.get(item_id, [])
        return similar_items[:top_k]


def _resolve_recommendations_path(path: Path) -> Path:
    """Resolve compact recommendation parquet path.

    Supported inputs:
    - a direct parquet file path;
    - a directory containing ``similar_items.parquet``;
    - a manifest JSON file with a known path key that points to compact output.
    """
    if path.suffix == ".json":
        return _resolve_from_manifest(path)

    if path.suffix:
        return path

    return path / DEFAULT_LOOKUP_FILENAME


def _resolve_from_manifest(manifest_path: Path) -> Path:
    """Read a manifest and resolve a compact recommendations path from it."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidate = _find_manifest_path(manifest)

    if candidate is None:
        keys = ", ".join(_LOOKUP_PATH_KEYS)
        raise ValueError(
            "Manifest does not contain a compact recommendations path. "
            f"Expected one of: {keys}"
        )

    candidate_path = Path(candidate)
    if candidate_path.is_absolute():
        return candidate_path

    return (manifest_path.parent / candidate_path).resolve()


def _find_manifest_path(manifest: dict[str, Any]) -> str | None:
    """Find compact recommendations path in flat or nested manifest data."""
    for key in _LOOKUP_PATH_KEYS:
        value = manifest.get(key)
        if isinstance(value, str):
            return value

    paths = manifest.get("paths")
    if isinstance(paths, dict):
        for key in _LOOKUP_PATH_KEYS:
            value = paths.get(key)
            if isinstance(value, str):
                return value

    return None


def _build_lookup_mapping(frame: pl.DataFrame) -> dict[int | str, list[int | str]]:
    """Build item_id -> rank-ordered similar items mapping."""
    item_col, list_col = schemas.WIDGET_OUTPUT_COLUMNS

    mapping: dict[int | str, list[int | str]] = {}
    for row in frame.select([item_col, list_col]).to_dicts():
        item_id = row[item_col]
        similar_items = row[list_col] or []
        mapping[item_id] = [item for item in similar_items if item is not None]

    return mapping
