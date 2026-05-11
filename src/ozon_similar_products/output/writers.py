"""Recommendation output writers."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.validation import (
    validate_recommendations,
    validate_widget_output,
)

FrameLike = pl.DataFrame | pl.LazyFrame

DEFAULT_DETAILED_FILENAME = "recommendations.parquet"
DEFAULT_WIDGET_FILENAME = "similar_items.parquet"


class RecommendationWriter:
    """Save detailed and widget recommendation outputs.

    The writer is responsible only for materializing already prepared
    recommendations. It does not calculate scores, does not select top-K, and
    does not perform lookup.
    """

    def save_detailed(
        self,
        recommendations: FrameLike,
        output_path: str | Path,
    ) -> Path:
        """Save item_id, similar_item_id, score, rank, source recommendations.

        Args:
            recommendations: Recommendations DataFrame or LazyFrame following
                the recommendations contract.
            output_path: Output parquet path or a directory. If a directory path
                is passed, ``recommendations.parquet`` is used as the file name.

        Returns:
            Path to the written parquet file.
        """
        validate_recommendations(recommendations)
        resolved_path = _resolve_parquet_output_path(
            output_path=output_path,
            default_filename=DEFAULT_DETAILED_FILENAME,
        )

        frame = _collect_if_lazy(recommendations)
        frame.write_parquet(resolved_path)
        return resolved_path

    def save_widget_format(
        self,
        recommendations: FrameLike,
        output_path: str | Path,
    ) -> Path:
        """Save compact item_id -> similar items output for lookup.

        Args:
            recommendations: Detailed recommendations DataFrame or LazyFrame.
                The input must follow the recommendations contract and contain
                ranked item-to-item rows.
            output_path: Output parquet path or a directory. If a directory path
                is passed, ``similar_items.parquet`` is used as the file name.

        Returns:
            Path to the written parquet file.
        """
        widget_output = self.to_widget_format(recommendations)
        resolved_path = _resolve_parquet_output_path(
            output_path=output_path,
            default_filename=DEFAULT_WIDGET_FILENAME,
        )

        widget_output.write_parquet(resolved_path)
        return resolved_path

    def to_widget_format(self, recommendations: FrameLike) -> pl.DataFrame:
        """Convert detailed recommendations to compact lookup format.

        The compact output contains one row per item_id and a rank-ordered list
        of similar items. The list column name follows the current project
        contract: ``similar_items_sku_list``.
        """
        validate_recommendations(recommendations)

        widget_output = (
            _as_lazy(recommendations)
            .filter(pl.col("item_id").is_not_null())
            .filter(pl.col("similar_item_id").is_not_null())
            .filter(pl.col("rank").is_not_null())
            .sort(["item_id", "rank", "similar_item_id"])
            .group_by("item_id", maintain_order=True)
            .agg(pl.col("similar_item_id").alias(schemas.WIDGET_OUTPUT_COLUMNS[1]))
            .select(schemas.WIDGET_OUTPUT_COLUMNS)
            .collect()
        )

        validate_widget_output(widget_output)
        return widget_output


def _collect_if_lazy(frame: FrameLike) -> pl.DataFrame:
    """Return an eager DataFrame for both DataFrame and LazyFrame inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame.collect()
    return frame


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    """Return a LazyFrame for both eager and lazy Polars inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


def _resolve_parquet_output_path(
    output_path: str | Path,
    default_filename: str,
) -> Path:
    """Resolve an output path and create its parent directory.

    If the passed path has a suffix, it is treated as a file path. Otherwise it
    is treated as a directory path and ``default_filename`` is appended.
    """
    path = Path(output_path)

    if path.suffix:
        resolved_path = path
    else:
        resolved_path = path / default_filename

    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    return resolved_path
