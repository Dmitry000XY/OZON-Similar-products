"""Recommendation output writers."""

from pathlib import Path

import polars as pl

from ozon_similar_products.data.validation import validate_recommendations, validate_widget_output


class RecommendationWriter:
    """Save detailed and widget recommendation outputs."""

    def save_detailed(
        self,
        recommendations: pl.DataFrame,
        output_path: str | Path,
    ) -> None:
        """Save item_id, similar_item_id, score, rank, source."""
        validate_recommendations(recommendations)
        raise NotImplementedError

    def save_widget_format(
        self,
        recommendations: pl.DataFrame,
        output_path: str | Path,
    ) -> None:
        """Save item_id, similar_items_sku_list."""
        validate_recommendations(recommendations)
        raise NotImplementedError