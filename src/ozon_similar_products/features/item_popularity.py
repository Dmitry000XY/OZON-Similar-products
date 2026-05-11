"""Item popularity builder for the calibrated multichannel MVP pipeline.

This module deliberately keeps popularity factual: it counts item interactions and
calibration shares by ``action_type``. It does not assign business weights, and it
does not compute recommendation scores. Weights belong to ``CoVisitationScorer``.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from ozon_similar_products.data.validation import (
    validate_action_type_distribution,
    validate_clean_events,
    validate_item_popularity,
)

FrameLike = pl.DataFrame | pl.LazyFrame

ITEM_INTERACTION_ACTION_TYPES = ("view", "click", "favorite", "to_cart")
ACTION_COUNT_COLUMNS = {
    "view": "views_count",
    "click": "clicks_count",
    "favorite": "favorites_count",
    "to_cart": "to_cart_count",
}


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    """Return a LazyFrame for both eager and lazy Polars inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


@dataclass(frozen=True)
class ItemPopularityBuilder:
    """Build factual item-popularity and action-type calibration tables.

    Public methods are named after the artifact they return:
    - ``build_item_popularity`` returns the production ``item_popularity`` table;
    - ``build_action_type_calibration_stats`` returns calibration shares for the scorer;
    - ``build_item_popularity_by_*`` methods return optional diagnostics for EDA.

    The builder works from clean events for the requested period. It intentionally
    has no ``aggregate_window`` method because exact ``unique_users`` cannot be
    reconstructed from pre-aggregated daily popularity tables.
    """

    item_action_types: Sequence[str] = ITEM_INTERACTION_ACTION_TYPES

    def build_item_popularity(self, events_clean: FrameLike) -> pl.DataFrame:
        """Build the main item popularity table from clean events.

        The input can be one day or the whole rolling/calibration window. Search
        rows, rows without ``item_id`` and action types outside ``item_action_types``
        are ignored.
        """
        validate_clean_events(events_clean)

        item_popularity = (
            self._item_events(events_clean)
            .group_by("item_id")
            .agg(self._base_aggregations(include_action_counts=True))
            .sort(["events_count", "item_id"], descending=[True, False])
            .collect()
        )

        validate_item_popularity(item_popularity)
        return item_popularity

    def build_action_type_calibration_stats(
            self,
            events_clean: FrameLike,
            calibration_start: str,
            calibration_end: str,
    ) -> pl.DataFrame:
        """Build action-type shares used by calibrated multichannel scoring.

        The result is a factual artifact. It stores how frequent each action type
        was on the calibration window; it does not transform these shares into
        effective weights.
        """
        validate_clean_events(events_clean)

        distribution = (
            self._item_events(events_clean)
            .group_by("action_type")
            .agg(
                pl.len().alias("events_count"),
                pl.col("user_id").n_unique().alias("unique_users"),
                pl.col("item_id").n_unique().alias("unique_items"),
            )
            .with_columns(
                (pl.col("events_count") / pl.col("events_count").sum()).alias("event_share"),
                pl.lit(calibration_start).alias("calibration_start"),
                pl.lit(calibration_end).alias("calibration_end"),
            )
            .select(
                "action_type",
                "events_count",
                "event_share",
                "unique_users",
                "unique_items",
                "calibration_start",
                "calibration_end",
            )
            .sort("action_type")
            .collect()
        )

        validate_action_type_distribution(distribution)
        return distribution

    def build_item_popularity_by_date(self, events_clean: FrameLike) -> pl.DataFrame:
        """Build item popularity diagnostics grouped by ``event_date`` and ``item_id``."""
        validate_clean_events(events_clean)
        return self._build_grouped_item_popularity(
            events_clean=events_clean,
            group_columns=["event_date", "item_id"],
            sort_columns=["event_date", "events_count", "item_id"],
            descending=[False, True, False],
            include_action_counts=True,
        )

    def build_item_popularity_by_action_type(self, events_clean: FrameLike) -> pl.DataFrame:
        """Build item popularity diagnostics grouped by ``item_id`` and ``action_type``."""
        validate_clean_events(events_clean)
        return self._build_grouped_item_popularity(
            events_clean=events_clean,
            group_columns=["item_id", "action_type"],
            sort_columns=["item_id", "events_count", "action_type"],
            descending=[False, True, False],
            include_action_counts=False,
        )

    def build_item_popularity_by_widget_name(self, events_clean: FrameLike) -> pl.DataFrame:
        """Build item popularity diagnostics grouped by ``item_id`` and ``widget_name``."""
        validate_clean_events(events_clean)
        return self._build_grouped_item_popularity(
            events_clean=events_clean,
            group_columns=["item_id", "widget_name"],
            sort_columns=["item_id", "events_count", "widget_name"],
            descending=[False, True, False],
            include_action_counts=True,
        )

    def _item_events(self, events_clean: FrameLike) -> pl.LazyFrame:
        """Return direct item-interaction events used by popularity artifacts."""
        return (
            _as_lazy(events_clean)
            .filter(pl.col("item_id").is_not_null())
            .filter(pl.col("action_type").is_in(self.item_action_types))
        )

    @staticmethod
    def _base_aggregations(include_action_counts: bool) -> list[pl.Expr]:
        """Return standard popularity aggregations."""
        aggregations: list[pl.Expr] = [
            pl.len().alias("events_count"),
            pl.col("user_id").n_unique().alias("unique_users"),
        ]

        if include_action_counts:
            aggregations.extend(
                (pl.col("action_type") == action_type).sum().alias(count_column)
                for action_type, count_column in ACTION_COUNT_COLUMNS.items()
            )

        return aggregations

    def _build_grouped_item_popularity(
            self,
            events_clean: FrameLike,
            group_columns: Sequence[str],
            sort_columns: Sequence[str],
            descending: Sequence[bool],
            include_action_counts: bool,
    ) -> pl.DataFrame:
        """Build grouped item-popularity diagnostics."""
        return (
            self._item_events(events_clean)
            .group_by(list(group_columns))
            .agg(self._base_aggregations(include_action_counts=include_action_counts))
            .sort(list(sort_columns), descending=list(descending))
            .collect()
        )
