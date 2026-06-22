"""Tests for scoring/output over prebuilt artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from ozon_similar_products.data import schemas
from ozon_similar_products.pipeline import scoring_output


def _pair_aggregates(rows: list[dict[str, Any]]) -> pl.DataFrame:
    frame = pl.DataFrame(rows)
    return frame.with_columns(
        pl.col("pair_count").cast(pl.Float64).alias("weighted_pair_count"),
        pl.col("view_count").cast(pl.Float64).alias("weighted_view_count"),
        pl.col("click_count").cast(pl.Float64).alias("weighted_click_count"),
        pl.col("favorite_count").cast(pl.Float64).alias("weighted_favorite_count"),
        pl.col("to_cart_count").cast(pl.Float64).alias("weighted_to_cart_count"),
    ).select(schemas.PAIR_AGGREGATES_COLUMNS)


def _item_popularity() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "item_id": [1, 2, 10, 11, 20, 21],
            "events_count": [20, 30, 10, 8, 12, 6],
            "unique_users": [5, 6, 3, 2, 4, 2],
            "views_count": [15, 20, 9, 7, 8, 6],
            "clicks_count": [3, 4, 1, 1, 2, 0],
            "favorites_count": [1, 2, 0, 0, 1, 0],
            "to_cart_count": [1, 4, 0, 0, 1, 0],
        }
    ).select(schemas.ITEM_POPULARITY_COLUMNS)


def _action_distribution() -> pl.DataFrame:
    return pl.DataFrame({column: [] for column in schemas.ACTION_TYPE_DISTRIBUTION_COLUMNS})


def _products() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "item_id": [1, 2, 10, 11, 20, 21],
            "name": ["item-1", "item-2", "item-10", "item-11", "item-20", "item-21"],
        }
    )


def _run_config(tmp_path: Path) -> dict[str, Any]:
    return {
        "pipeline": {"allow_empty_latest_update": False},
        "topk": {"top_k": 2},
        "scoring": {"method": "pair_count", "count_source": "raw"},
        "business": {"fallback": {"enabled": False}},
        "outputs": {
            "root_dir": (tmp_path / "outputs").as_posix(),
            "latest_dir": (tmp_path / "latest").as_posix(),
        },
    }


def test_run_scoring_output_from_artifacts_matches_single_frame_for_bucket_parts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    part_one = _pair_aggregates(
        [
            {
                "item_id": 1,
                "similar_item_id": 10,
                "pair_count": 5,
                "view_count": 5,
                "click_count": 0,
                "favorite_count": 0,
                "to_cart_count": 0,
                "unique_users": 2,
                "unique_sessions": 2,
                "window_start": "2024-04-23",
                "window_end": "2024-04-23",
            },
            {
                "item_id": 1,
                "similar_item_id": 11,
                "pair_count": 3,
                "view_count": 0,
                "click_count": 0,
                "favorite_count": 0,
                "to_cart_count": 3,
                "unique_users": 1,
                "unique_sessions": 1,
                "window_start": "2024-04-23",
                "window_end": "2024-04-23",
            },
        ]
    )
    part_two = _pair_aggregates(
        [
            {
                "item_id": 2,
                "similar_item_id": 20,
                "pair_count": 7,
                "view_count": 7,
                "click_count": 0,
                "favorite_count": 0,
                "to_cart_count": 0,
                "unique_users": 3,
                "unique_sessions": 3,
                "window_start": "2024-04-23",
                "window_end": "2024-04-23",
            },
            {
                "item_id": 2,
                "similar_item_id": 21,
                "pair_count": 2,
                "view_count": 0,
                "click_count": 2,
                "favorite_count": 0,
                "to_cart_count": 0,
                "unique_users": 1,
                "unique_sessions": 1,
                "window_start": "2024-04-23",
                "window_end": "2024-04-23",
            },
        ]
    )
    single_frame = pl.concat([part_one, part_two], how="vertical")

    monkeypatch.setattr(scoring_output, "load_configs", lambda project_root: {})
    monkeypatch.setattr(
        scoring_output,
        "load_products",
        lambda *_args, **kwargs: _products().select(kwargs["columns"]),
    )

    common_kwargs = {
        "config": _run_config(tmp_path),
        "item_popularity": _item_popularity(),
        "action_distribution": _action_distribution(),
        "train_until_date": "2024-04-23",
        "lookback_days": 1,
        "window_start": "2024-04-23",
        "window_end": "2024-04-23",
        "update_latest": False,
        "row_counts": {"pair_aggregates": 4},
    }
    single_result = scoring_output.run_scoring_output_from_artifacts(
        **common_kwargs,
        pair_aggregates=single_frame,
        output_dir=tmp_path / "single",
        run_id="single",
    )
    parts_result = scoring_output.run_scoring_output_from_artifacts(
        **common_kwargs,
        pair_aggregates=(part_one.lazy(), part_two.lazy()),
        output_dir=tmp_path / "parts",
        run_id="parts",
    )

    single_recommendations = pl.read_parquet(
        single_result.detailed_recommendations_path
    ).sort(["item_id", "rank", "similar_item_id"])
    parts_recommendations = pl.read_parquet(
        parts_result.detailed_recommendations_path
    ).sort(["item_id", "rank", "similar_item_id"])

    assert parts_recommendations.to_dicts() == single_recommendations.to_dicts()
    assert parts_result.manifest["rows"]["pair_scores"] == single_result.manifest["rows"]["pair_scores"]
    assert (
        parts_result.manifest["rows"]["recommendations"]
        == single_result.manifest["rows"]["recommendations"]
    )
