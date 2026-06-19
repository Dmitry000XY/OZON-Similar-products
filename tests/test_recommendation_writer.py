"""Tests for recommendation output writer."""

from pathlib import Path

import polars as pl
import pytest

from ozon_similar_products.data.validation import validate_widget_output
from ozon_similar_products.output.writers import RecommendationWriter


def _recommendations() -> pl.DataFrame:
    """Build a small recommendations table with channel diagnostic columns."""
    return pl.DataFrame(
        {
            "item_id": [1, 1, 2],
            "similar_item_id": [10, 11, 20],
            "score": [100.0, 80.0, 50.0],
            "rank": [1, 2, 1],
            "source": ["behavioral", "behavioral", "behavioral"],
            "pair_count": [100, 80, 50],
            "view_count": [40, 30, 20],
            "click_count": [30, 25, 15],
            "favorite_count": [20, 15, 10],
            "to_cart_count": [10, 10, 5],
            "unique_users": [40, 30, 20],
            "unique_sessions": [50, 35, 25],
        }
    )


def _recommendations_out_of_rank_order() -> pl.DataFrame:
    """Build recommendations intentionally not sorted by rank."""
    return pl.DataFrame(
        {
            "item_id": [1, 1, 1, 2, 2],
            "similar_item_id": [30, 10, 20, 50, 40],
            "score": [70.0, 100.0, 80.0, 60.0, 90.0],
            "rank": [3, 1, 2, 2, 1],
            "source": [
                "behavioral",
                "behavioral",
                "behavioral",
                "behavioral",
                "behavioral",
            ],
        }
    )


def _products() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "item_id": [1, 10, 11, 20],
            "name": ["Anchor", "Candidate A", "Candidate B", "Candidate C"],
        }
    )


def test_save_detailed_writes_parquet_to_explicit_file_path(tmp_path: Path) -> None:
    """Detailed writer should save recommendations to a concrete parquet path."""
    recommendations = _recommendations()
    output_path = tmp_path / "nested" / "detailed.parquet"

    written_path = RecommendationWriter().save_detailed(recommendations, output_path)

    assert written_path == output_path
    assert output_path.is_file()
    saved = pl.read_parquet(output_path)

    assert saved.columns == recommendations.columns
    assert saved.to_dicts() == recommendations.to_dicts()


def test_save_detailed_accepts_directory_path(tmp_path: Path) -> None:
    """When output_path is a directory, the default file name should be used."""
    recommendations = _recommendations()
    output_dir = tmp_path / "run_001"

    written_path = RecommendationWriter().save_detailed(recommendations, output_dir)

    output_path = output_dir / "detailed.parquet"
    assert written_path == output_path
    assert output_path.is_file()
    saved = pl.read_parquet(output_path)

    assert saved.to_dicts() == recommendations.to_dicts()


def test_save_detailed_accepts_lazy_frame(tmp_path: Path) -> None:
    """Detailed writer should accept both DataFrame and LazyFrame inputs."""
    recommendations = _recommendations()
    output_path = tmp_path / "detailed.parquet"

    RecommendationWriter().save_detailed(recommendations.lazy(), output_path)

    saved = pl.read_parquet(output_path)
    assert saved.to_dicts() == recommendations.to_dicts()


def test_save_detailed_preserves_channel_diagnostic_columns(tmp_path: Path) -> None:
    """Extra channel diagnostic columns should remain in detailed output."""
    recommendations = _recommendations()
    output_path = tmp_path / "detailed.parquet"

    RecommendationWriter().save_detailed(recommendations, output_path)

    saved = pl.read_parquet(output_path)

    assert "pair_count" in saved.columns
    assert "view_count" in saved.columns
    assert "click_count" in saved.columns
    assert "favorite_count" in saved.columns
    assert "to_cart_count" in saved.columns
    assert "unique_users" in saved.columns
    assert "unique_sessions" in saved.columns
    assert "weight_sum" not in saved.columns


def test_save_enriched_writes_product_names_and_required_columns(tmp_path: Path) -> None:
    recommendations = _recommendations_out_of_rank_order()
    output_path = tmp_path / "recommendations" / "enriched.parquet"

    written_path = RecommendationWriter().save_enriched(
        recommendations,
        _products(),
        output_path,
    )

    assert written_path == output_path
    saved = pl.read_parquet(output_path)
    assert saved.columns == [
        "item_id",
        "item_name",
        "similar_item_id",
        "similar_item_name",
        "rank",
        "score",
        "source",
    ]
    assert saved.to_dicts() == [
        {
            "item_id": 1,
            "item_name": "Anchor",
            "similar_item_id": 10,
            "similar_item_name": "Candidate A",
            "rank": 1,
            "score": 100.0,
            "source": "behavioral",
        },
        {
            "item_id": 1,
            "item_name": "Anchor",
            "similar_item_id": 20,
            "similar_item_name": "Candidate C",
            "rank": 2,
            "score": 80.0,
            "source": "behavioral",
        },
        {
            "item_id": 1,
            "item_name": "Anchor",
            "similar_item_id": 30,
            "similar_item_name": None,
            "rank": 3,
            "score": 70.0,
            "source": "behavioral",
        },
        {
            "item_id": 2,
            "item_name": None,
            "similar_item_id": 40,
            "similar_item_name": None,
            "rank": 1,
            "score": 90.0,
            "source": "behavioral",
        },
        {
            "item_id": 2,
            "item_name": None,
            "similar_item_id": 50,
            "similar_item_name": None,
            "rank": 2,
            "score": 60.0,
            "source": "behavioral",
        },
    ]


def test_save_enriched_accepts_lazy_frames(tmp_path: Path) -> None:
    output_path = tmp_path / "enriched.parquet"

    RecommendationWriter().save_enriched(
        _recommendations_out_of_rank_order().lazy(),
        _products().lazy(),
        output_path,
    )

    assert output_path.is_file()


def test_save_enriched_validates_product_name_columns(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="product_information"):
        RecommendationWriter().save_enriched(
            _recommendations(),
            pl.DataFrame({"item_id": [1]}),
            tmp_path / "enriched.parquet",
        )


@pytest.mark.parametrize(
    "missing_column",
    ["item_id", "similar_item_id", "score", "rank", "source"],
)
def test_save_detailed_validates_recommendations_contract(
    tmp_path: Path,
    missing_column: str,
) -> None:
    """Detailed writer should fail when required recommendation columns are absent."""
    invalid_recommendations = _recommendations().drop(missing_column)
    output_path = tmp_path / "detailed.parquet"

    with pytest.raises(ValueError, match="missing expected columns"):
        RecommendationWriter().save_detailed(invalid_recommendations, output_path)

    assert not output_path.exists()


def test_to_widget_format_groups_similar_items_by_rank_order() -> None:
    """Widget output should contain rank-ordered similar item lists per item."""
    recommendations = _recommendations_out_of_rank_order()

    widget_output = RecommendationWriter().to_widget_format(recommendations)

    validate_widget_output(widget_output)
    assert widget_output.columns == ["item_id", "similar_items_sku_list"]
    assert widget_output.to_dicts() == [
        {"item_id": 1, "similar_items_sku_list": [10, 20, 30]},
        {"item_id": 2, "similar_items_sku_list": [40, 50]},
    ]


def test_to_widget_format_uses_similar_item_id_as_tie_breaker() -> None:
    """Equal ranks should be ordered deterministically by similar_item_id."""
    recommendations = pl.DataFrame(
        {
            "item_id": [1, 1, 1],
            "similar_item_id": [30, 10, 20],
            "score": [1.0, 1.0, 1.0],
            "rank": [1, 1, 1],
            "source": ["behavioral", "behavioral", "behavioral"],
        }
    )

    widget_output = RecommendationWriter().to_widget_format(recommendations)

    assert widget_output["similar_items_sku_list"].to_list() == [[10, 20, 30]]


def test_to_widget_format_drops_null_item_candidates_and_ranks() -> None:
    """Rows unusable for lookup should not appear in the compact output."""
    recommendations = pl.DataFrame(
        {
            "item_id": [1, 1, None, 2],
            "similar_item_id": [10, None, 30, 20],
            "score": [100.0, 80.0, 70.0, 60.0],
            "rank": [1, 2, 1, None],
            "source": ["behavioral", "behavioral", "behavioral", "behavioral"],
        }
    )

    widget_output = RecommendationWriter().to_widget_format(recommendations)

    assert widget_output.to_dicts() == [
        {"item_id": 1, "similar_items_sku_list": [10]},
    ]


def test_save_widget_format_writes_parquet_to_explicit_file_path(tmp_path: Path) -> None:
    """Widget writer should save compact recommendations to a concrete parquet path."""
    recommendations = _recommendations_out_of_rank_order()
    output_path = tmp_path / "nested" / "lookup.parquet"

    written_path = RecommendationWriter().save_widget_format(recommendations, output_path)

    assert written_path == output_path
    assert output_path.is_file()
    saved = pl.read_parquet(output_path)

    validate_widget_output(saved)
    assert saved.to_dicts() == [
        {"item_id": 1, "similar_items_sku_list": [10, 20, 30]},
        {"item_id": 2, "similar_items_sku_list": [40, 50]},
    ]


def test_save_widget_format_accepts_directory_path(tmp_path: Path) -> None:
    """When output_path is a directory, the default widget file name should be used."""
    recommendations = _recommendations_out_of_rank_order()
    output_dir = tmp_path / "widget" / "run_001"

    written_path = RecommendationWriter().save_widget_format(recommendations, output_dir)

    output_path = output_dir / "lookup.parquet"
    assert written_path == output_path
    assert output_path.is_file()


def test_save_widget_format_accepts_lazy_frame(tmp_path: Path) -> None:
    """Widget writer should accept both DataFrame and LazyFrame inputs."""
    recommendations = _recommendations_out_of_rank_order()
    output_path = tmp_path / "lookup.parquet"

    RecommendationWriter().save_widget_format(recommendations.lazy(), output_path)

    saved = pl.read_parquet(output_path)
    assert saved.to_dicts() == [
        {"item_id": 1, "similar_items_sku_list": [10, 20, 30]},
        {"item_id": 2, "similar_items_sku_list": [40, 50]},
    ]


@pytest.mark.parametrize(
    "missing_column",
    ["item_id", "similar_item_id", "score", "rank", "source"],
)
def test_save_widget_format_validates_recommendations_contract(
    tmp_path: Path,
    missing_column: str,
) -> None:
    """Widget writer should fail when required recommendation columns are absent."""
    invalid_recommendations = _recommendations().drop(missing_column)
    output_path = tmp_path / "lookup.parquet"

    with pytest.raises(ValueError, match="missing expected columns"):
        RecommendationWriter().save_widget_format(invalid_recommendations, output_path)

    assert not output_path.exists()
