"""Build directed multichannel item-item pairs from user sessions."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.data.validation import (
    validate_daily_item_pairs,
    validate_daily_pair_counts,
    validate_daily_pair_session_keys,
    validate_daily_pair_user_keys,
    validate_sessions,
)
from ozon_similar_products.retrieval.decay import DistanceDecayConfig

FrameLike = pl.DataFrame | pl.LazyFrame


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    """Return a LazyFrame for both eager and lazy Polars inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


def _empty_daily_pairs() -> pl.DataFrame:
    """Return an empty daily-pairs DataFrame with the public contract columns."""
    return empty_contract_frame(schemas.DAILY_ITEM_PAIRS_COLUMNS)


def _empty_daily_pair_counts() -> pl.DataFrame:
    """Return an empty daily pair-counts DataFrame with the public contract columns."""
    return empty_contract_frame(schemas.DAILY_PAIR_COUNTS_COLUMNS)


def _empty_daily_pair_user_keys() -> pl.DataFrame:
    """Return an empty daily pair-user-keys DataFrame with the public contract columns."""
    return empty_contract_frame(schemas.DAILY_PAIR_USER_KEYS_COLUMNS)


def _empty_daily_pair_session_keys() -> pl.DataFrame:
    """Return an empty daily pair-session-keys DataFrame with the public contract columns."""
    return empty_contract_frame(schemas.DAILY_PAIR_SESSION_KEYS_COLUMNS)


def _build_signal_priority(action_types: Sequence[str]) -> dict[str, int]:
    """Build strongest-signal priorities from action order.

    The first action type is the weakest signal, the last one is the strongest.
    In production this order should come from config, for example:
    ``view -> click -> favorite -> to_cart``.
    """
    return {
        action_type: priority
        for priority, action_type in enumerate(action_types, start=1)
    }


def _priority_expr(priority: Mapping[str, int]) -> pl.Expr:
    """Build a Polars expression that maps action_type to priority."""
    expr = pl.lit(0)
    for action_type, action_priority in priority.items():
        expr = pl.when(pl.col("action_type") == action_type).then(action_priority).otherwise(expr)
    return expr.cast(pl.Int64).alias("signal_priority")


def _weighted_sum_expr(signal_type: str, output_column: str) -> pl.Expr:
    return (
        pl.when(pl.col("signal_type") == signal_type)
        .then(pl.col("distance_weight"))
        .otherwise(0.0)
        .sum()
        .alias(output_column)
    )


def _max_distance_filter(distance_decay: DistanceDecayConfig) -> pl.Expr:
    if distance_decay.max_distance is None:
        return pl.lit(True)
    return pl.col("position_distance") <= distance_decay.max_distance


@dataclass(frozen=True)
class DailyPairStats:
    """Compact daily pair artifacts derived from raw directed pair rows."""

    counts: pl.DataFrame
    user_keys: pl.DataFrame
    session_keys: pl.DataFrame
    raw_pair_rows: int


@dataclass(frozen=True)
class ItemPairBuilder:
    """Build directed item-item pairs from sessions.

    The builder does not apply weights. It keeps action channels instead of
    compressing them into a single score too early. Each item inside a session
    is collapsed to one strongest item-level signal, then directed pairs are
    created. For pair A -> B, downstream channel is the target signal of B.

    Signal strength order is configurable through ``item_action_types`` or
    ``signal_priority``. By default, we use the public action order from
    ``schemas.ITEM_SIGNAL_TYPES``. Production pipeline code should pass values
    from ``configs/baseline.yaml`` instead of hardcoding priorities here.
    """

    max_items_per_session: int = 50
    item_action_types: Sequence[str] = field(
        default_factory=lambda: tuple(schemas.ITEM_SIGNAL_TYPES)
    )
    signal_priority: Mapping[str, int] | None = None
    distance_decay: DistanceDecayConfig = field(default_factory=DistanceDecayConfig)

    def __post_init__(self) -> None:
        if self.max_items_per_session < 2:
            raise ValueError("max_items_per_session must be at least 2")
        if not self.item_action_types:
            raise ValueError("item_action_types must not be empty")

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "ItemPairBuilder":
        """Create builder from project config.

        Expected config shape:
        ```yaml
        pipeline:
          max_items_per_session: 50
        events:
          item_action_types: [view, click, favorite, to_cart]
        item_pair_builder:
          signal_priority:
            view: 1
            click: 2
            favorite: 3
            to_cart: 4
        ```
        """
        pipeline_config = config.get("pipeline", {})
        events_config = config.get("events", {})
        pair_builder_config = config.get("item_pair_builder", {})

        raw_action_types = events_config.get("item_action_types", schemas.ITEM_SIGNAL_TYPES)
        if isinstance(raw_action_types, str):
            item_action_types = (raw_action_types,)
        elif isinstance(raw_action_types, Sequence):
            item_action_types = tuple(raw_action_types)
        else:
            raise TypeError("events.item_action_types must be a string or a sequence of strings")

        if not item_action_types or any(
                not isinstance(action_type, str) or not action_type.strip()
                for action_type in item_action_types
        ):
            raise ValueError(
                "events.item_action_types must contain non-empty string values"
            )
        signal_priority = pair_builder_config.get("signal_priority")
        if signal_priority is not None:
            signal_priority = {
                str(action_type): int(priority)
                for action_type, priority in signal_priority.items()
            }

        return cls(
            max_items_per_session=int(pipeline_config.get("max_items_per_session", 50)),
            item_action_types=item_action_types,
            signal_priority=signal_priority,
            distance_decay=DistanceDecayConfig.from_config(config),
        )

    def transform_day(self, sessions: FrameLike) -> pl.DataFrame:
        """Build directed item-item pairs for one sessions partition."""
        validate_sessions(sessions)

        session_items = self._build_session_items(sessions)
        valid_sessions = self._build_valid_sessions(session_items)

        valid_session_items = session_items.join(
            valid_sessions,
            on=["user_id", "session_index"],
            how="inner",
        )

        pairs = (
            valid_session_items
            .join(
                valid_session_items,
                on=["user_id", "session_index"],
                how="inner",
                suffix="_similar",
            )
            .filter(pl.col("item_id") != pl.col("item_id_similar"))
            .select(
                pl.col("session_start_date").cast(pl.Date, strict=False).alias("pair_date"),
                pl.col("item_id"),
                pl.col("item_id_similar").alias("similar_item_id"),
                pl.col("user_id"),
                pl.col("session_index"),
                pl.col("item_action_type").alias("source_action_type"),
                pl.col("item_action_type_similar").alias("target_action_type"),
                pl.col("item_action_type_similar").alias("signal_type"),
                pl.col("item_position").alias("source_position"),
                pl.col("item_position_similar").alias("target_position"),
            )
            .with_columns(
                (pl.col("target_position") - pl.col("source_position"))
                .abs()
                .cast(pl.Int64)
                .alias("position_distance")
            )
            .filter(_max_distance_filter(self.distance_decay))
            .with_columns(
                self.distance_decay.weight_expr(pl.col("position_distance"))
                .cast(pl.Float64)
                .alias("distance_weight")
            )
            .sort(["pair_date", "item_id", "similar_item_id", "user_id", "session_index"])
            .collect()
        )

        if pairs.is_empty():
            pairs = _empty_daily_pairs()

        validate_daily_item_pairs(pairs)
        return pairs

    def build_daily_pair_stats(self, sessions: FrameLike) -> DailyPairStats:
        """Build compact daily pair statistics for one sessions partition.

        This keeps the old pair semantics but stores compact daily artifacts:
        count aggregates, unique user keys and unique session keys. The raw pair
        rows count is preserved for pipeline manifests.
        """
        pairs = self.transform_day(sessions)
        raw_pair_rows = pairs.height

        if pairs.is_empty():
            stats = DailyPairStats(
                counts=_empty_daily_pair_counts(),
                user_keys=_empty_daily_pair_user_keys(),
                session_keys=_empty_daily_pair_session_keys(),
                raw_pair_rows=0,
            )
            validate_daily_pair_counts(stats.counts)
            validate_daily_pair_user_keys(stats.user_keys)
            validate_daily_pair_session_keys(stats.session_keys)
            return stats

        counts = (
            pairs.group_by(["pair_date", "item_id", "similar_item_id"])
            .agg(
                pl.len().alias("pair_count"),
                (pl.col("signal_type") == "view").sum().alias("view_count"),
                (pl.col("signal_type") == "click").sum().alias("click_count"),
                (pl.col("signal_type") == "favorite").sum().alias("favorite_count"),
                (pl.col("signal_type") == "to_cart").sum().alias("to_cart_count"),
                pl.col("distance_weight").sum().alias("weighted_pair_count"),
                _weighted_sum_expr("view", "weighted_view_count"),
                _weighted_sum_expr("click", "weighted_click_count"),
                _weighted_sum_expr("favorite", "weighted_favorite_count"),
                _weighted_sum_expr("to_cart", "weighted_to_cart_count"),
            )
            .select(schemas.DAILY_PAIR_COUNTS_COLUMNS)
            .sort(["pair_date", "item_id", "similar_item_id"])
        )

        user_keys = (
            pairs.select(schemas.DAILY_PAIR_USER_KEYS_COLUMNS)
            .unique()
            .sort(["pair_date", "item_id", "similar_item_id", "user_id"])
        )

        session_keys = (
            pairs.select(schemas.DAILY_PAIR_SESSION_KEYS_COLUMNS)
            .unique()
            .sort(["pair_date", "item_id", "similar_item_id", "user_id", "session_index"])
        )

        validate_daily_pair_counts(counts)
        validate_daily_pair_user_keys(user_keys)
        validate_daily_pair_session_keys(session_keys)

        return DailyPairStats(
            counts=counts,
            user_keys=user_keys,
            session_keys=session_keys,
            raw_pair_rows=raw_pair_rows,
        )

    def _build_session_items(self, sessions: FrameLike) -> pl.LazyFrame:
        """Collapse repeated item actions inside a session to one strongest signal."""
        priority = self._resolved_signal_priority()
        return (
            _as_lazy(sessions)
            .select(
                "user_id",
                "session_index",
                "session_start_date",
                "event_date",
                "timestamp",
                "item_id",
                "action_type",
            )
            .filter(pl.col("item_id").is_not_null())
            .filter(pl.col("action_type").is_in(list(self.item_action_types)))
            .with_columns(_priority_expr(priority))
            .sort(["user_id", "session_index", "timestamp", "item_id", "action_type"])
            .with_row_index("__event_position")
            .group_by(["user_id", "session_index", "session_start_date", "item_id"])
            .agg(
                pl.col("action_type")
                .sort_by(pl.col("signal_priority"), descending=True)
                .first()
                .alias("item_action_type"),
                pl.col("signal_priority").max().alias("item_signal_priority"),
                pl.col("__event_position").min().cast(pl.Int64).alias("item_position"),
            )
        )

    def _build_valid_sessions(self, session_items: pl.LazyFrame) -> pl.LazyFrame:
        """Keep sessions that can create pairs and are not too long/noisy."""
        return (
            session_items.group_by(["user_id", "session_index"])
            .agg(pl.len().alias("items_count"))
            .filter(pl.col("items_count").is_between(2, self.max_items_per_session))
            .select("user_id", "session_index")
        )

    def _resolved_signal_priority(self) -> Mapping[str, int]:
        """Return explicit priority map or derive it from configured action order."""
        if self.signal_priority is not None:
            return self.signal_priority
        return _build_signal_priority(self.item_action_types)
