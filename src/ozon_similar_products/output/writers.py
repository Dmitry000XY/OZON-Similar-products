"""Recommendation output writers."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from ozon_similar_products.data.validation import (
    validate_recommendations,
    validate_widget_output,
)

FrameLike = pl.DataFrame | pl.LazyFrame


class RecommendationWriter:
    """Save detailed and widget recommendation outputs."""

    def save_detailed(
        self,
        recommendations: FrameLike,
        output_path: str | Path,
    ) -> None:
        """Save detailed recommendations as a parquet file.

        The method expects a DataFrame or LazyFrame that follows the
        recommendations contract. Extra diagnostic columns are preserved in the
        saved file because they are useful for manual review and debugging.

        If output_path points to a directory or has no file suffix, the default
        file name ``recommendations.parquet`` is used inside that directory.
        """
        validate_recommendations(recommendations)

        recommendations_frame = _as_data_frame(recommendations)
        detailed_path = _resolve_output_path(
            output_path=output_path,
            default_filename="recommendations.parquet",
        )
        detailed_path.parent.mkdir(parents=True, exist_ok=True)

        recommendations_frame.write_parquet(detailed_path)

    def save_widget_format(
        self,
        recommendations: pl.DataFrame,
        output_path: str | Path,
    ) -> None:
        """Save item_id, similar_items_sku_list."""
        validate_widget_output(recommendations)
        raise NotImplementedError


def _as_data_frame(frame: FrameLike) -> pl.DataFrame:
    """Return an eager DataFrame for both eager and lazy Polars inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame.collect()
    return frame


def _resolve_output_path(output_path: str | Path, default_filename: str) -> Path:
    """Resolve a file path for parquet output.

    A path with a suffix is treated as a concrete file path. A path without a
    suffix is treated as a directory and the default file name is appended.
    """
    path = Path(output_path)

    if path.suffix:
        return path

    return path / default_filename
