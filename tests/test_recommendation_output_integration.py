"""Integration tests for the recommendation output layer."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from ozon_similar_products.output.lookup import SimilarItemsLookup
from ozon_similar_products.output.writers import RecommendationWriter
from ozon_similar_products.retrieval.topk import TopKSelector


def _pair_scores() -> pl.DataFrame:
    """Build synthetic pair scores using the current channel-count contract."""
    return pl.DataFrame(
        {
            "item_id": [1, 1, 1, 1, 1, 1, 2, 2],
            "similar_item_id": [1, 10, 10, 11, 12, 13, 20, 21],
            "score": [100.0, 12.0, 8.0, 10.0, 10.0, 99.0, 5.0, 6.0],
            "pair_count": [100, 12, 8, 10, 10, 1, 5, 6],
            "view_count": [80, 8, 6, 7, 7, 1, 4, 5],
            "click_count": [10, 2, 1, 2, 2, 0, 1, 1],
            "favorite_count": [5, 1, 1, 1, 1, 0, 0, 0],
            "to_cart_count": [5, 1, 0, 0, 0, 0, 0, 0],
            "unique_users": [50, 7, 4, 6, 6, 1, 3, 4],
            "unique_sessions": [60, 8, 5, 7, 7, 1, 4, 5],
        }
    )


def test_recommendation_output_layer_end_to_end(tmp_path: Path) -> None:
    """Run pair_scores through top-K, writers, latest manifest and lookup."""
    pair_scores = _pair_scores()
    writer = RecommendationWriter()
    run_dir = tmp_path / "outputs" / "recommendations" / "runs" / "run_001"
    latest_dir = tmp_path / "outputs" / "recommendations" / "latest"

    recommendations = TopKSelector(top_k=2, min_pair_count=2).select(pair_scores)

    assert recommendations.select(["item_id", "similar_item_id", "rank"]).to_dicts() == [
        {"item_id": 1, "similar_item_id": 10, "rank": 1},
        {"item_id": 1, "similar_item_id": 11, "rank": 2},
        {"item_id": 2, "similar_item_id": 21, "rank": 1},
        {"item_id": 2, "similar_item_id": 20, "rank": 2},
    ]
    assert "weight_sum" not in recommendations.columns
    assert "to_cart_count" in recommendations.columns

    detailed_path = writer.save_detailed(recommendations, run_dir / "detailed")
    widget_path = writer.save_widget_format(recommendations, run_dir / "widget")
    manifest_path = writer.save_manifest(
        {
            "run_id": "run_001",
            "top_k": 2,
            "score_method": "calibrated_multichannel",
            "min_pair_count": 2,
            "min_unique_users": None,
            "min_unique_sessions": None,
            "paths": {
                "detailed_recommendations_path": "detailed/recommendations.parquet",
                "widget_recommendations_path": "widget/similar_items.parquet",
            },
        },
        run_dir,
    )
    latest_manifest_path = writer.update_latest_manifest(manifest_path, latest_dir)

    assert detailed_path == run_dir / "detailed" / "recommendations.parquet"
    assert widget_path == run_dir / "widget" / "similar_items.parquet"
    assert manifest_path == run_dir / "manifest.json"
    assert latest_manifest_path == latest_dir / "manifest.json"

    latest_manifest = json.loads(latest_manifest_path.read_text(encoding="utf-8"))
    assert latest_manifest["paths"]["widget_recommendations_path"] == (
        "../runs/run_001/widget/similar_items.parquet"
    )

    lookup = SimilarItemsLookup(latest_manifest_path)

    assert lookup.get_similar_items(1, top_k=10) == [10, 11]
    assert lookup.get_similar_items(1, top_k=1) == [10]
    assert lookup.get_similar_items(2, top_k=10) == [21, 20]
    assert lookup.get_similar_items(999, top_k=10) == []
