"""Tests for recommendation output writer."""

from pathlib import Path

import polars as pl
import pytest

from ozon_similar_products.output.writers import RecommendationWriter


def _recommendations() -> pl.DataFrame:
    """Build a small recommendations table with diagnostic columns."""
    return pl.DataFrame(
        {
            "item_id": [1, 1, 2],
            "similar_item_id": [10, 11, 20],
            "score": [100.0, 80.0, 50.0],
            "rank": [1, 2, 1],
            "source": ["behavioral", "behavioral", "behavioral"],
            "pair_count": [100, 80, 50],
            "weight_sum": [120.0, 90.0, 60.0],
            "unique_users": [40, 30, 20],
            "unique_sessions": [50, 35, 25],
        }
    )


def test_save_detailed_writes_parquet_to_explicit_file_path(tmp_path: Path) -> None:
    """Detailed writer should save recommendations to a concrete parquet path."""
    recommendations = _recommendations()
    output_path = tmp_path / "nested" / "recommendations.parquet"

    RecommendationWriter().save_detailed(recommendations, output_path)

    assert output_path.is_file()
    saved = pl.read_parquet(output_path)

    assert saved.columns == recommendations.columns
    assert saved.to_dicts() == recommendations.to_dicts()


def test_save_detailed_accepts_directory_path(tmp_path: Path) -> None:
    """When output_path is a directory, the default file name should be used."""
    recommendations = _recommendations()
    output_dir = tmp_path / "run_001"

    RecommendationWriter().save_detailed(recommendations, output_dir)

    output_path = output_dir / "recommendations.parquet"
    assert output_path.is_file()
    saved = pl.read_parquet(output_path)

    assert saved.to_dicts() == recommendations.to_dicts()


def test_save_detailed_accepts_lazy_frame(tmp_path: Path) -> None:
    """Detailed writer should accept both DataFrame and LazyFrame inputs."""
    recommendations = _recommendations()
    output_path = tmp_path / "recommendations.parquet"

    RecommendationWriter().save_detailed(recommendations.lazy(), output_path)

    saved = pl.read_parquet(output_path)
    assert saved.to_dicts() == recommendations.to_dicts()


def test_save_detailed_preserves_diagnostic_columns(tmp_path: Path) -> None:
    """Extra diagnostic columns should remain in the saved detailed output."""
    recommendations = _recommendations()
    output_path = tmp_path / "recommendations.parquet"

    RecommendationWriter().save_detailed(recommendations, output_path)

    saved = pl.read_parquet(output_path)

    assert "pair_count" in saved.columns
    assert "weight_sum" in saved.columns
    assert "unique_users" in saved.columns
    assert "unique_sessions" in saved.columns


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
    output_path = tmp_path / "recommendations.parquet"

    with pytest.raises(ValueError, match="missing expected columns"):
        RecommendationWriter().save_detailed(invalid_recommendations, output_path)

    assert not output_path.exists()
