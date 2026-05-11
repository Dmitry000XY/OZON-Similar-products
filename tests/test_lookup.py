"""Tests for SimilarItemsLookup."""

import json
from pathlib import Path

import polars as pl
import pytest

from ozon_similar_products.output.lookup import SimilarItemsLookup


def _compact_recommendations() -> pl.DataFrame:
    """Build compact recommendations with integer item ids."""
    return pl.DataFrame(
        {
            "item_id": [1, 2],
            "similar_items_sku_list": [[10, 20, 30], [40, 50]],
        },
        schema={
            "item_id": pl.Int64,
            "similar_items_sku_list": pl.List(pl.Int64),
        },
    )


def test_lookup_returns_similar_items_from_compact_parquet(tmp_path: Path) -> None:
    """Lookup should read compact output and return rank-ordered items."""
    path = tmp_path / "similar_items.parquet"
    _compact_recommendations().write_parquet(path)

    lookup = SimilarItemsLookup(path)

    assert lookup.get_similar_items(1) == [10, 20, 30]
    assert lookup.get_similar_items(2) == [40, 50]


def test_lookup_limits_result_by_top_k(tmp_path: Path) -> None:
    """Lookup should return only the requested number of similar items."""
    path = tmp_path / "similar_items.parquet"
    _compact_recommendations().write_parquet(path)

    lookup = SimilarItemsLookup(path)

    assert lookup.get_similar_items(1, top_k=2) == [10, 20]


def test_lookup_returns_empty_list_for_missing_item(tmp_path: Path) -> None:
    """Unknown item ids should not raise errors in MVP lookup."""
    path = tmp_path / "similar_items.parquet"
    _compact_recommendations().write_parquet(path)

    lookup = SimilarItemsLookup(path)

    assert lookup.get_similar_items(999) == []


def test_lookup_accepts_directory_path(tmp_path: Path) -> None:
    """Directory input should resolve to similar_items.parquet inside it."""
    output_dir = tmp_path / "latest"
    output_dir.mkdir()
    _compact_recommendations().write_parquet(output_dir / "similar_items.parquet")

    lookup = SimilarItemsLookup(output_dir)

    assert lookup.get_similar_items(1, top_k=1) == [10]


def test_lookup_accepts_manifest_path(tmp_path: Path) -> None:
    """Manifest input should resolve the compact recommendations path."""
    output_dir = tmp_path / "run_001"
    output_dir.mkdir()
    compact_path = output_dir / "similar_items.parquet"
    _compact_recommendations().write_parquet(compact_path)

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps({"widget_recommendations_path": "similar_items.parquet"}),
        encoding="utf-8",
    )

    lookup = SimilarItemsLookup(manifest_path)

    assert lookup.get_similar_items(1) == [10, 20, 30]


def test_lookup_accepts_nested_manifest_paths(tmp_path: Path) -> None:
    """Manifest paths can also be stored inside a paths object."""
    output_dir = tmp_path / "latest"
    output_dir.mkdir()
    compact_path = output_dir / "similar_items.parquet"
    _compact_recommendations().write_parquet(compact_path)

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps({"paths": {"compact_recommendations_path": "similar_items.parquet"}}),
        encoding="utf-8",
    )

    lookup = SimilarItemsLookup(manifest_path)

    assert lookup.get_similar_items(2) == [40, 50]


def test_lookup_rejects_manifest_without_compact_path(tmp_path: Path) -> None:
    """Manifest should point to compact recommendations."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"run_id": "run_001"}), encoding="utf-8")

    with pytest.raises(ValueError, match="compact recommendations path"):
        SimilarItemsLookup(manifest_path)


def test_lookup_validates_compact_output_contract(tmp_path: Path) -> None:
    """Lookup should fail when compact output contract is invalid."""
    path = tmp_path / "similar_items.parquet"
    pl.DataFrame({"item_id": [1], "wrong_column": [[10]]}).write_parquet(path)

    with pytest.raises(ValueError, match="missing expected columns"):
        SimilarItemsLookup(path)


def test_lookup_rejects_non_positive_top_k(tmp_path: Path) -> None:
    """top_k should be positive."""
    path = tmp_path / "similar_items.parquet"
    _compact_recommendations().write_parquet(path)
    lookup = SimilarItemsLookup(path)

    with pytest.raises(ValueError, match="top_k must be a positive integer"):
        lookup.get_similar_items(1, top_k=0)


def test_lookup_supports_string_item_ids_when_saved_as_strings(tmp_path: Path) -> None:
    """String item ids should work when compact output uses string ids."""
    path = tmp_path / "similar_items.parquet"
    pl.DataFrame(
        {
            "item_id": ["sku-1"],
            "similar_items_sku_list": [["sku-2", "sku-3"]],
        },
        schema={
            "item_id": pl.String,
            "similar_items_sku_list": pl.List(pl.String),
        },
    ).write_parquet(path)

    lookup = SimilarItemsLookup(path)

    assert lookup.get_similar_items("sku-1") == ["sku-2", "sku-3"]
