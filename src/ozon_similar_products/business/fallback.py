"""Fallback layer for post-top-K business rules.

Current implementation is intentionally MVP/local:
- disabled by default;
- uses Python-level row assembly in ``FallbackMerger``;
- should be rewritten to Polars-native operations before production-scale usage.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.validation import (
    validate_item_popularity,
    validate_recommendations,
)

FrameLike = pl.DataFrame | pl.LazyFrame

_DIAGNOSTIC_DEFAULTS: dict[str, int] = {
    "pair_count": 0,
    "view_count": 0,
    "click_count": 0,
    "favorite_count": 0,
    "to_cart_count": 0,
    "unique_users": 0,
    "unique_sessions": 0,
}


def _collect_if_lazy(frame: FrameLike) -> pl.DataFrame:
    """Return an eager frame for both DataFrame and LazyFrame inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame.collect()
    return frame


def _as_mapping(value: Any) -> dict[str, Any]:
    """Return mapping copy or an empty mapping fallback."""
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise TypeError("Expected bool value for fallback config")


def _as_positive_int(value: Any, default: int, parameter_name: str) -> int:
    if value is None:
        parsed = default
    elif isinstance(value, bool):
        raise ValueError(f"{parameter_name} must be a positive integer")
    elif isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError as error:
            raise ValueError(f"{parameter_name} must be a positive integer") from error
    else:
        raise TypeError(f"{parameter_name} must be a positive integer")

    if parsed <= 0:
        raise ValueError(f"{parameter_name} must be a positive integer")
    return parsed


def _as_non_empty_str(value: Any, default: str, parameter_name: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise TypeError(f"{parameter_name} must be a string")
    if not value:
        raise ValueError(f"{parameter_name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class FallbackConfig:
    """Fallback policy config."""

    enabled: bool = False
    top_k: int = 20
    source_label: str = "fallback"
    candidate_pool_size: int = 200
    include_cold_start_items: bool = True
    min_item_events: int = 1

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        top_k: int,
    ) -> "FallbackConfig":
        """Build fallback config from pipeline YAML mapping."""
        business_config = _as_mapping(config.get("business", {}))
        fallback_config = _as_mapping(config.get("fallback", {}))
        fallback_config = {
            **fallback_config,
            **_as_mapping(business_config.get("fallback", {})),
        }

        return cls(
            enabled=_as_bool(fallback_config.get("enabled"), default=False),
            top_k=_as_positive_int(
                fallback_config.get("top_k"),
                default=top_k,
                parameter_name="fallback.top_k",
            ),
            source_label=_as_non_empty_str(
                fallback_config.get("source_label"),
                default="fallback",
                parameter_name="fallback.source_label",
            ),
            candidate_pool_size=_as_positive_int(
                fallback_config.get("candidate_pool_size"),
                default=max(top_k * 10, 100),
                parameter_name="fallback.candidate_pool_size",
            ),
            include_cold_start_items=_as_bool(
                fallback_config.get("include_cold_start_items"),
                default=True,
            ),
            min_item_events=_as_positive_int(
                fallback_config.get("min_item_events"),
                default=1,
                parameter_name="fallback.min_item_events",
            ),
        )

    def __post_init__(self) -> None:
        if self.top_k <= 0:
            raise ValueError("fallback.top_k must be a positive integer")
        if not self.source_label:
            raise ValueError("fallback.source_label must be a non-empty string")
        if self.candidate_pool_size <= 0:
            raise ValueError("fallback.candidate_pool_size must be a positive integer")
        if self.min_item_events <= 0:
            raise ValueError("fallback.min_item_events must be a positive integer")


@dataclass(frozen=True)
class FallbackCandidateBuilder:
    """Build globally ranked fallback candidates from item popularity."""

    config: FallbackConfig

    def build(self, item_popularity: FrameLike) -> list[int | str]:
        """Return candidate item ids sorted by fallback priority."""
        validate_item_popularity(item_popularity)
        popularity = _collect_if_lazy(item_popularity)

        if popularity.is_empty():
            return []

        candidates = (
            popularity
            .filter(pl.col("events_count") >= self.config.min_item_events)
            .sort(["events_count", "item_id"], descending=[True, False])
            .select("item_id")
            .head(self.config.candidate_pool_size)
            .to_series()
            .to_list()
        )
        return [candidate for candidate in candidates if candidate is not None]


@dataclass(frozen=True)
class FallbackMerger:
    """Merge recommendations with fallback candidates (MVP/local implementation)."""

    config: FallbackConfig

    def merge(
        self,
        recommendations: FrameLike,
        fallback_candidates: Sequence[int | str],
        *,
        source_item_ids: Sequence[int | str],
    ) -> pl.DataFrame:
        """Fill top-K lists using fallback candidates when needed."""
        validate_recommendations(recommendations)
        baseline = _collect_if_lazy(recommendations)
        extra_columns = [
            column for column in baseline.columns
            if column not in schemas.RECOMMENDATIONS_COLUMNS
        ]

        baseline_by_item = self._group_by_item_id(baseline)
        final_rows: list[dict[str, Any]] = []
        for source_item_id in source_item_ids:
            final_rows.extend(
                self._build_rows_for_source(
                    source_item_id=source_item_id,
                    baseline_rows=baseline_by_item.get(source_item_id, []),
                    fallback_candidates=fallback_candidates,
                    extra_columns=extra_columns,
                )
            )

        if not final_rows:
            empty_columns = {column: [] for column in [*schemas.RECOMMENDATIONS_COLUMNS, *extra_columns]}
            return pl.DataFrame(empty_columns)

        columns_order = [*schemas.RECOMMENDATIONS_COLUMNS, *extra_columns]
        merged = pl.DataFrame(final_rows).select(columns_order)
        validate_recommendations(merged)
        return merged

    def _group_by_item_id(self, recommendations: pl.DataFrame) -> dict[int | str, list[dict[str, Any]]]:
        if recommendations.is_empty():
            return {}

        grouped: dict[int | str, list[dict[str, Any]]] = {}
        grouped_frames = recommendations.partition_by("item_id", as_dict=True, maintain_order=True)
        for item_key, frame in grouped_frames.items():
            item_id = item_key[0] if isinstance(item_key, tuple) else item_key
            rows = (
                frame
                .sort(["rank", "score", "similar_item_id"], descending=[False, True, False])
                .iter_rows(named=True)
            )
            grouped[item_id] = [dict(row) for row in rows]
        return grouped

    def _build_rows_for_source(
        self,
        *,
        source_item_id: int | str,
        baseline_rows: Sequence[dict[str, Any]],
        fallback_candidates: Sequence[int | str],
        extra_columns: Sequence[str],
    ) -> list[dict[str, Any]]:
        selected_rows = list(baseline_rows[:self.config.top_k])
        taken_similar_ids = {
            row["similar_item_id"]
            for row in selected_rows
            if row.get("similar_item_id") is not None
        }
        taken_similar_ids.add(source_item_id)

        for candidate in fallback_candidates:
            if len(selected_rows) >= self.config.top_k:
                break
            if candidate in taken_similar_ids:
                continue

            selected_rows.append(
                self._fallback_row(
                    source_item_id=source_item_id,
                    similar_item_id=candidate,
                    extra_columns=extra_columns,
                )
            )
            taken_similar_ids.add(candidate)

        output_rows: list[dict[str, Any]] = []
        for rank, row in enumerate(selected_rows, start=1):
            normalized = dict(row)
            normalized["item_id"] = source_item_id
            normalized["rank"] = rank
            output_rows.append(normalized)
        return output_rows

    def _fallback_row(
        self,
        *,
        source_item_id: int | str,
        similar_item_id: int | str,
        extra_columns: Sequence[str],
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "item_id": source_item_id,
            "similar_item_id": similar_item_id,
            "score": 0.0,
            "rank": 0,
            "source": self.config.source_label,
        }
        for column in extra_columns:
            row[column] = _DIAGNOSTIC_DEFAULTS.get(column)
        return row


def merge_fallback_candidates(
    recommendations: FrameLike,
    item_popularity: FrameLike,
    *,
    config: FallbackConfig,
) -> pl.DataFrame:
    """Merge behavioral recommendations with fallback candidates."""
    validate_recommendations(recommendations)
    baseline = _collect_if_lazy(recommendations)
    validate_item_popularity(item_popularity)
    popularity = _collect_if_lazy(item_popularity)

    if baseline.is_empty() and popularity.is_empty():
        return baseline

    candidate_builder = FallbackCandidateBuilder(config=config)
    candidates = candidate_builder.build(popularity)
    if not candidates:
        return baseline

    if config.include_cold_start_items:
        source_item_ids = (
            popularity
            .select("item_id")
            .drop_nulls()
            .to_series()
            .to_list()
        )
    else:
        source_item_ids = (
            baseline
            .select("item_id")
            .drop_nulls()
            .unique(maintain_order=True)
            .to_series()
            .to_list()
        )

    merger = FallbackMerger(config=config)
    return merger.merge(
        recommendations=baseline,
        fallback_candidates=candidates,
        source_item_ids=source_item_ids,
    )


@dataclass(frozen=True)
class FallbackLayer:
    """Post-top-K fallback layer wrapper."""

    config: FallbackConfig

    def apply(
        self,
        recommendations: FrameLike,
        *,
        item_popularity: FrameLike,
    ) -> pl.DataFrame:
        """Apply fallback policy to already ranked recommendations."""
        if not self.config.enabled:
            validate_recommendations(recommendations)
            return _collect_if_lazy(recommendations)

        return merge_fallback_candidates(
            recommendations=recommendations,
            item_popularity=item_popularity,
            config=self.config,
        )
