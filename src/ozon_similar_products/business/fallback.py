"""Fallback layer for post-top-K business rules.

Current implementation is intentionally MVP/local:
- disabled by default;
- precomputes metadata/popularity indexes before Python-level row assembly;
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
    validate_product_information,
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

_POPULARITY_SORT_COLUMNS = [
    "unique_users",
    "to_cart_count",
    "favorites_count",
    "clicks_count",
    "views_count",
    "events_count",
    "item_id",
]
_POPULARITY_SORT_DESCENDING = [True, True, True, True, True, True, False]

_FALLBACK_CATEGORY_TYPE = "fallback_category_type_popular"
_FALLBACK_CATEGORY = "fallback_category_popular"
_FALLBACK_TYPE = "fallback_type_popular"
_FALLBACK_BRAND = "fallback_brand_popular"
_FALLBACK_GLOBAL = "fallback_global_popular"

FALLBACK_SOURCE_LABELS = [
    _FALLBACK_CATEGORY_TYPE,
    _FALLBACK_CATEGORY,
    _FALLBACK_TYPE,
    _FALLBACK_BRAND,
    _FALLBACK_GLOBAL,
]


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


def _as_optional_positive_int(value: Any, parameter_name: str) -> int | None:
    if value is None:
        return None
    return _as_positive_int(
        value,
        default=1,
        parameter_name=parameter_name,
    )


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
    global_candidate_pool_size: int | None = None
    metadata_candidate_pool_size: int | None = None
    include_cold_start_items: bool = True
    min_item_events: int = 1
    enable_category_type: bool = True
    enable_category: bool = True
    enable_type: bool = True
    enable_brand: bool = False
    enable_global: bool = True
    include_catalog_only_sources: bool = False

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
            global_candidate_pool_size=_as_optional_positive_int(
                fallback_config.get("global_candidate_pool_size"),
                parameter_name="fallback.global_candidate_pool_size",
            ),
            metadata_candidate_pool_size=_as_optional_positive_int(
                fallback_config.get("metadata_candidate_pool_size"),
                parameter_name="fallback.metadata_candidate_pool_size",
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
            enable_category_type=_as_bool(
                fallback_config.get("enable_category_type"),
                default=True,
            ),
            enable_category=_as_bool(
                fallback_config.get("enable_category"),
                default=True,
            ),
            enable_type=_as_bool(
                fallback_config.get("enable_type"),
                default=True,
            ),
            enable_brand=_as_bool(
                fallback_config.get("enable_brand"),
                default=False,
            ),
            enable_global=_as_bool(
                fallback_config.get("enable_global"),
                default=True,
            ),
            include_catalog_only_sources=_as_bool(
                fallback_config.get("include_catalog_only_sources"),
                default=False,
            ),
        )

    def __post_init__(self) -> None:
        if self.top_k <= 0:
            raise ValueError("fallback.top_k must be a positive integer")
        if not self.source_label:
            raise ValueError("fallback.source_label must be a non-empty string")
        if self.candidate_pool_size <= 0:
            raise ValueError("fallback.candidate_pool_size must be a positive integer")
        if self.global_candidate_pool_size is not None and self.global_candidate_pool_size <= 0:
            raise ValueError("fallback.global_candidate_pool_size must be a positive integer")
        if self.metadata_candidate_pool_size is not None and self.metadata_candidate_pool_size <= 0:
            raise ValueError("fallback.metadata_candidate_pool_size must be a positive integer")
        if self.min_item_events <= 0:
            raise ValueError("fallback.min_item_events must be a positive integer")

    @property
    def effective_global_candidate_pool_size(self) -> int:
        return self.global_candidate_pool_size or self.candidate_pool_size

    @property
    def effective_metadata_candidate_pool_size(self) -> int:
        return self.metadata_candidate_pool_size or self.candidate_pool_size


@dataclass(frozen=True)
class FallbackCandidateBuilder:
    """Build popularity-ranked fallback candidate pools."""

    config: FallbackConfig

    def build(self, item_popularity: FrameLike) -> list[int | str]:
        """Return globally popular candidate item ids sorted by fallback priority."""
        return (
            self.ranked_popularity(item_popularity)
            .select("item_id")
            .head(self.config.effective_global_candidate_pool_size)
            .to_series()
            .to_list()
        )

    def ranked_popularity(self, item_popularity: FrameLike) -> pl.DataFrame:
        """Return popularity rows sorted by deterministic fallback priority."""
        validate_item_popularity(item_popularity)
        popularity = _collect_if_lazy(item_popularity)

        if popularity.is_empty():
            return popularity.select(schemas.ITEM_POPULARITY_COLUMNS)

        return (
            popularity
            .select(schemas.ITEM_POPULARITY_COLUMNS)
            .filter(pl.col("events_count") >= self.config.min_item_events)
            .with_columns(
                pl.col(column).fill_null(0).alias(column)
                for column in schemas.ITEM_POPULARITY_COLUMNS
                if column != "item_id"
            )
            .sort(
                _POPULARITY_SORT_COLUMNS,
                descending=_POPULARITY_SORT_DESCENDING,
            )
        )


def _empty_product_information() -> pl.DataFrame:
    return pl.DataFrame({column: [] for column in schemas.PRODUCT_INFORMATION_COLUMNS})


def _collect_product_information(product_information: FrameLike | None) -> pl.DataFrame:
    if product_information is None:
        return _empty_product_information()

    validate_product_information(product_information)
    return _collect_if_lazy(product_information).select(schemas.PRODUCT_INFORMATION_COLUMNS)


def _metadata_by_item_id(product_information: pl.DataFrame) -> dict[int | str, dict[str, Any]]:
    if product_information.is_empty():
        return {}

    metadata: dict[int | str, dict[str, Any]] = {}
    for row in product_information.unique(subset=["item_id"], keep="first").iter_rows(named=True):
        item_id = row.get("item_id")
        if item_id is not None:
            metadata[item_id] = dict(row)
    return metadata


@dataclass(frozen=True)
class FallbackIndex:
    """Precomputed fallback candidate lists keyed by source metadata."""

    global_candidates: list[int | str]
    by_category_type: dict[tuple[Any, Any], list[int | str]]
    by_category: dict[Any, list[int | str]]
    by_type: dict[Any, list[int | str]]
    by_brand: dict[Any, list[int | str]]
    metadata_by_item_id: dict[int | str, dict[str, Any]]

    @property
    def is_empty(self) -> bool:
        return not self.global_candidates


@dataclass(frozen=True)
class FallbackIndexBuilder:
    """Build reusable fallback indexes from popularity left-joined to metadata."""

    config: FallbackConfig

    def build(
        self,
        item_popularity: FrameLike,
        product_information: FrameLike | None = None,
    ) -> FallbackIndex:
        """Return popularity-ordered candidate indexes built once per fallback run."""
        ranked_popularity = FallbackCandidateBuilder(
            config=self.config
        ).ranked_popularity(item_popularity)
        products = _collect_product_information(product_information).unique(
            subset=["item_id"],
            keep="first",
        )
        metadata_by_id = _metadata_by_item_id(products)

        if ranked_popularity.is_empty():
            return FallbackIndex(
                global_candidates=[],
                by_category_type={},
                by_category={},
                by_type={},
                by_brand={},
                metadata_by_item_id=metadata_by_id,
            )

        if products.is_empty():
            candidate_rows = ranked_popularity.with_columns(
                pl.lit(False).alias("__has_product_information")
            )
        else:
            indexed_products = products.with_columns(
                pl.lit(True).alias("__has_product_information")
            )
            candidate_rows = ranked_popularity.join(
                indexed_products,
                on="item_id",
                how="left",
            )

        global_candidates: list[int | str] = []
        by_category_type: dict[tuple[Any, Any], list[int | str]] = {}
        by_category: dict[Any, list[int | str]] = {}
        by_type: dict[Any, list[int | str]] = {}
        by_brand: dict[Any, list[int | str]] = {}
        global_limit = self.config.effective_global_candidate_pool_size
        metadata_limit = self.config.effective_metadata_candidate_pool_size

        for row in candidate_rows.iter_rows(named=True):
            item_id = row.get("item_id")
            if item_id is None:
                continue

            if len(global_candidates) < global_limit:
                global_candidates.append(item_id)

            if not row.get("__has_product_information"):
                continue

            category_id = row.get("category_id")
            item_type = row.get("type")
            brand = row.get("brand")

            if _has_value(category_id) and _has_value(item_type):
                self._append_limited(
                    by_category_type,
                    (category_id, item_type),
                    item_id,
                    limit=metadata_limit,
                )
            if _has_value(category_id):
                self._append_limited(
                    by_category,
                    category_id,
                    item_id,
                    limit=metadata_limit,
                )
            if _has_value(item_type):
                self._append_limited(
                    by_type,
                    item_type,
                    item_id,
                    limit=metadata_limit,
                )
            if _has_value(brand):
                self._append_limited(
                    by_brand,
                    brand,
                    item_id,
                    limit=metadata_limit,
                )

        return FallbackIndex(
            global_candidates=global_candidates,
            by_category_type=by_category_type,
            by_category=by_category,
            by_type=by_type,
            by_brand=by_brand,
            metadata_by_item_id=metadata_by_id,
        )

    @staticmethod
    def _append_limited(
        mapping: dict[Any, list[int | str]],
        key: Any,
        item_id: int | str,
        *,
        limit: int,
    ) -> None:
        candidates = mapping.setdefault(key, [])
        if len(candidates) < limit:
            candidates.append(item_id)


def _has_value(value: Any) -> bool:
    return value is not None


def _source_item_ids(
    *,
    baseline: pl.DataFrame,
    item_popularity: pl.DataFrame,
    product_information: pl.DataFrame,
    config: FallbackConfig,
) -> list[int | str]:
    item_ids: list[int | str] = []

    if not baseline.is_empty():
        item_ids.extend(
            baseline
            .select("item_id")
            .drop_nulls()
            .unique(maintain_order=True)
            .to_series()
            .to_list()
        )

    if config.include_cold_start_items and not item_popularity.is_empty():
        item_ids.extend(
            item_popularity
            .select("item_id")
            .drop_nulls()
            .unique(maintain_order=True)
            .to_series()
            .to_list()
        )

    if config.include_catalog_only_sources and not product_information.is_empty():
        item_ids.extend(
            product_information
            .select("item_id")
            .drop_nulls()
            .unique(maintain_order=True)
            .to_series()
            .to_list()
        )

    return list(dict.fromkeys(item_ids))


@dataclass(frozen=True)
class FallbackMerger:
    """Merge recommendations with precomputed fallback candidate indexes."""

    config: FallbackConfig

    def merge(
        self,
        recommendations: FrameLike,
        *,
        item_popularity: FrameLike | None = None,
        product_information: FrameLike | None = None,
        fallback_index: FallbackIndex | None = None,
        source_item_ids: Sequence[int | str],
    ) -> pl.DataFrame:
        """Fill top-K lists using direct metadata/global index lookups."""
        validate_recommendations(recommendations)
        baseline = _collect_if_lazy(recommendations)
        if fallback_index is None:
            if item_popularity is None:
                raise ValueError("item_popularity is required when fallback_index is not provided")
            fallback_index = FallbackIndexBuilder(config=self.config).build(
                item_popularity=item_popularity,
                product_information=product_information,
            )

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
                    fallback_index=fallback_index,
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
        fallback_index: FallbackIndex,
        extra_columns: Sequence[str],
    ) -> list[dict[str, Any]]:
        selected_rows: list[dict[str, Any]] = []
        taken_similar_ids: set[int | str] = set()
        for row in baseline_rows:
            similar_item_id = row.get("similar_item_id")
            if similar_item_id is None:
                continue
            if similar_item_id == source_item_id or similar_item_id in taken_similar_ids:
                continue

            selected_rows.append(dict(row))
            taken_similar_ids.add(similar_item_id)
            if len(selected_rows) >= self.config.top_k:
                break

        taken_similar_ids.add(source_item_id)

        for source_label, candidates in self._fallback_levels_for_source(
            source_item_id=source_item_id,
            fallback_index=fallback_index,
        ):
            for candidate in candidates:
                if len(selected_rows) >= self.config.top_k:
                    break
                if candidate in taken_similar_ids:
                    continue

                selected_rows.append(
                    self._fallback_row(
                        source_item_id=source_item_id,
                        similar_item_id=candidate,
                        source_label=source_label,
                        extra_columns=extra_columns,
                    )
                )
                taken_similar_ids.add(candidate)
            if len(selected_rows) >= self.config.top_k:
                break

        output_rows: list[dict[str, Any]] = []
        for rank, row in enumerate(selected_rows, start=1):
            normalized = dict(row)
            normalized["item_id"] = source_item_id
            normalized["rank"] = rank
            output_rows.append(normalized)
        return output_rows

    def _fallback_levels_for_source(
        self,
        *,
        source_item_id: int | str,
        fallback_index: FallbackIndex,
    ) -> list[tuple[str, list[int | str]]]:
        source_metadata = fallback_index.metadata_by_item_id.get(source_item_id)
        levels: list[tuple[str, list[int | str]]] = []

        if source_metadata is not None:
            category_id = source_metadata.get("category_id")
            item_type = source_metadata.get("type")
            brand = source_metadata.get("brand")

            if self.config.enable_category_type and _has_value(category_id) and _has_value(item_type):
                levels.append((
                    _FALLBACK_CATEGORY_TYPE,
                    fallback_index.by_category_type.get((category_id, item_type), []),
                ))
            if self.config.enable_category and _has_value(category_id):
                levels.append((
                    _FALLBACK_CATEGORY,
                    fallback_index.by_category.get(category_id, []),
                ))
            if self.config.enable_type and _has_value(item_type):
                levels.append((
                    _FALLBACK_TYPE,
                    fallback_index.by_type.get(item_type, []),
                ))
            if self.config.enable_brand and _has_value(brand):
                levels.append((
                    _FALLBACK_BRAND,
                    fallback_index.by_brand.get(brand, []),
                ))

        if self.config.enable_global:
            levels.append((
                _FALLBACK_GLOBAL,
                fallback_index.global_candidates,
            ))

        return levels

    def _fallback_row(
        self,
        *,
        source_item_id: int | str,
        similar_item_id: int | str,
        source_label: str,
        extra_columns: Sequence[str],
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "item_id": source_item_id,
            "similar_item_id": similar_item_id,
            "score": 0.0,
            "rank": 0,
            "source": source_label,
        }
        for column in extra_columns:
            row[column] = _DIAGNOSTIC_DEFAULTS.get(column)
        return row


def merge_fallback_candidates(
    recommendations: FrameLike,
    item_popularity: FrameLike,
    *,
    config: FallbackConfig,
    product_information: FrameLike | None = None,
) -> pl.DataFrame:
    """Merge behavioral recommendations with fallback candidates."""
    validate_recommendations(recommendations)
    baseline = _collect_if_lazy(recommendations)
    validate_item_popularity(item_popularity)
    popularity = _collect_if_lazy(item_popularity)
    products = _collect_product_information(product_information)

    if baseline.is_empty() and popularity.is_empty() and not config.include_catalog_only_sources:
        return baseline

    fallback_index = FallbackIndexBuilder(config=config).build(
        item_popularity=popularity,
        product_information=products,
    )
    if fallback_index.is_empty:
        return baseline

    source_item_ids = _source_item_ids(
        baseline=baseline,
        item_popularity=popularity,
        product_information=products,
        config=config,
    )

    merger = FallbackMerger(config=config)
    return merger.merge(
        recommendations=baseline,
        fallback_index=fallback_index,
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
        product_information: FrameLike | None = None,
    ) -> pl.DataFrame:
        """Apply fallback policy to already ranked recommendations."""
        if not self.config.enabled:
            validate_recommendations(recommendations)
            return _collect_if_lazy(recommendations)

        return merge_fallback_candidates(
            recommendations=recommendations,
            item_popularity=item_popularity,
            product_information=product_information,
            config=self.config,
        )
