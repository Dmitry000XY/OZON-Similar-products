"""Tests for recommendation run manifest writing."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from ozon_similar_products.output.lookup import SimilarItemsLookup
from ozon_similar_products.output.writers import RecommendationWriter


def _compact_output() -> pl.DataFrame:
    """Build compact recommendations for manifest lookup tests."""
    return pl.DataFrame(
        {
            "item_id": [1, 2],
            "similar_items_sku_list": [[10, 20, 30], [40, 50]],
        }
    )


def test_save_manifest_writes_json_to_explicit_file_path(tmp_path: Path) -> None:
    """Manifest writer should save run metadata as JSON."""
    manifest = {
        "run_id": "run_001",
        "created_at": datetime(2026, 5, 11, 19, 30),
        "score_method": "calibrated_multichannel",
        "top_k": 20,
        "paths": {
            "widget_recommendations_path": Path("widget/run_001/lookup.parquet"),
        },
    }
    output_path = tmp_path / "runs" / "run_001" / "manifest.json"

    written_path = RecommendationWriter().save_manifest(manifest, output_path)

    assert written_path == output_path
    assert output_path.is_file()

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["run_id"] == "run_001"
    assert saved["created_at"] == "2026-05-11T19:30:00"
    assert saved["score_method"] == "calibrated_multichannel"
    assert saved["paths"]["widget_recommendations_path"] == (
        "widget/run_001/lookup.parquet"
    )


def test_save_manifest_accepts_directory_path(tmp_path: Path) -> None:
    """When output_path is a directory, manifest.json should be used."""
    manifest = {"run_id": "run_001", "top_k": 20}
    output_dir = tmp_path / "runs" / "run_001"

    written_path = RecommendationWriter().save_manifest(manifest, output_dir)

    assert written_path == output_dir / "manifest.json"
    assert written_path.is_file()


def test_save_manifest_rejects_non_mapping_manifest(tmp_path: Path) -> None:
    """Manifest payload must be a JSON object."""
    invalid_manifest: Any = ["not", "a", "mapping"]

    with pytest.raises(TypeError, match="manifest must be a mapping"):
        RecommendationWriter().save_manifest(invalid_manifest, tmp_path)


def test_update_latest_manifest_rebases_paths_for_lookup(tmp_path: Path) -> None:
    """Latest manifest should point to compact output from its new location."""
    writer = RecommendationWriter()
    run_dir = tmp_path / "outputs" / "recommendations" / "runs" / "run_001"
    widget_path = run_dir / "widget" / "lookup.parquet"
    manifest_path = run_dir / "manifest.json"
    latest_dir = tmp_path / "outputs" / "recommendations" / "latest"

    widget_path.parent.mkdir(parents=True, exist_ok=True)
    _compact_output().write_parquet(widget_path)
    writer.save_manifest(
        {
            "run_id": "run_001",
            "paths": {
                "widget_recommendations_path": "widget/lookup.parquet",
            },
        },
        manifest_path,
    )

    latest_manifest_path = writer.update_latest_manifest(manifest_path, latest_dir)

    assert latest_manifest_path == latest_dir / "manifest.json"
    latest_manifest = json.loads(latest_manifest_path.read_text(encoding="utf-8"))
    assert latest_manifest["paths"]["widget_recommendations_path"] == (
        "../runs/run_001/widget/lookup.parquet"
    )

    lookup = SimilarItemsLookup(latest_manifest_path)
    assert lookup.get_similar_items(1, top_k=2) == [10, 20]


def test_update_latest_manifest_supports_flat_path_key(tmp_path: Path) -> None:
    """Path rebasing should work for flat manifest path fields too."""
    writer = RecommendationWriter()
    run_dir = tmp_path / "runs" / "run_001"
    widget_path = run_dir / "lookup.parquet"
    manifest_path = run_dir / "manifest.json"
    latest_dir = tmp_path / "latest"

    widget_path.parent.mkdir(parents=True, exist_ok=True)
    _compact_output().write_parquet(widget_path)
    writer.save_manifest(
        {
            "run_id": "run_001",
            "widget_recommendations_path": "lookup.parquet",
        },
        manifest_path,
    )

    latest_manifest_path = writer.update_latest_manifest(manifest_path, latest_dir)

    lookup = SimilarItemsLookup(latest_manifest_path)
    assert lookup.get_similar_items(2) == [40, 50]


def test_update_latest_manifest_rejects_non_object_json(tmp_path: Path) -> None:
    """Run manifest JSON must contain an object."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("[]", encoding="utf-8")

    with pytest.raises(TypeError, match="manifest JSON must contain an object"):
        RecommendationWriter().update_latest_manifest(manifest_path, tmp_path / "latest")
