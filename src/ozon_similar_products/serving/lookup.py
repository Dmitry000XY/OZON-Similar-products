"""Lookup helper for saved similar items recommendations."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.validation import validate_widget_output
from ozon_similar_products.output.manifest import (
    COMPACT_RECOMMENDATIONS_PATH_KEYS,
    find_compact_recommendations_path,
    load_manifest,
)

DEFAULT_LOOKUP_FILENAME = "lookup.parquet"


class SimilarItemsLookup:
    """Read saved compact recommendations and return similar items."""

    def __init__(self, recommendations_path: str | Path) -> None:
        self.recommendations_path = Path(recommendations_path)
        self.resolved_recommendations_path = _resolve_recommendations_path(
            self.recommendations_path
        )
        self.recommendations = pl.read_parquet(self.resolved_recommendations_path)
        validate_widget_output(self.recommendations)
        self._items_by_item_id = _build_lookup_mapping(self.recommendations)

    def get_similar_items(self, item_id: int | str, top_k: int = 10) -> list[int | str]:
        """Return top-K similar items for ``item_id``."""
        if top_k <= 0:
            raise ValueError("top_k must be a positive integer")

        similar_items = self._items_by_item_id.get(item_id, [])
        return similar_items[:top_k]


def _resolve_recommendations_path(path: Path) -> Path:
    """Resolve compact recommendation parquet path."""
    if path.suffix == ".json":
        return _resolve_from_manifest(path)

    if path.suffix:
        return path

    return path / DEFAULT_LOOKUP_FILENAME


def _resolve_from_manifest(manifest_path: Path) -> Path:
    """Read a manifest and resolve compact recommendations path."""
    candidate = find_compact_recommendations_path(load_manifest(manifest_path))

    if candidate is None:
        keys = ", ".join(COMPACT_RECOMMENDATIONS_PATH_KEYS)
        raise ValueError(
            "Manifest does not contain a compact recommendations path. "
            f"Expected one of: {keys}"
        )

    candidate_path = Path(candidate)
    if candidate_path.is_absolute():
        return candidate_path

    return (manifest_path.parent / candidate_path).resolve()


def _build_lookup_mapping(frame: pl.DataFrame) -> dict[int | str, list[int | str]]:
    """Build item_id -> rank-ordered similar items mapping."""
    item_col, list_col = schemas.WIDGET_OUTPUT_COLUMNS

    mapping: dict[int | str, list[int | str]] = {}
    for row in frame.select([item_col, list_col]).iter_rows(named=True):
        item_id = row[item_col]
        similar_items = row[list_col] or []
        mapping[item_id] = [item for item in similar_items if item is not None]

    return mapping
