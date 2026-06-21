"""Offline metrics for item-to-item recommendation evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import polars as pl

from ozon_similar_products.data.validation import validate_item_popularity, validate_recommendations
from ozon_similar_products.evaluation.ground_truth import validate_ground_truth

FrameLike = pl.DataFrame | pl.LazyFrame

ACTION_TYPES = ("view", "click", "favorite", "to_cart")
DEFAULT_RANKING_RELEVANT_ACTION_TYPES = ("click", "favorite", "to_cart")
DEFAULT_MIN_RANKING_RELEVANCE = 0.3
RECOMMENDATION_METRIC_COLUMNS = ["item_id", "similar_item_id", "rank", "score", "source"]
GROUND_TRUTH_METRIC_COLUMNS = [
    "item_id",
    "relevant_item_id",
    "relevance",
    "target_action_type",
    "view_count",
    "click_count",
    "favorite_count",
    "to_cart_count",
]
FALLBACK_LAYER_SOURCES = {
    "fallback_category_type_popular": "fallback_category_type_share_at_k",
    "fallback_category_popular": "fallback_category_share_at_k",
    "fallback_type_popular": "fallback_type_share_at_k",
    "fallback_brand_popular": "fallback_brand_share_at_k",
    "fallback_global_popular": "fallback_global_share_at_k",
}


@dataclass(frozen=True)
class OfflineMetrics:
    """Container for key offline metrics."""

    hit_rate_at_k: float | None = None
    recall_at_k: float | None = None
    ndcg_at_k: float | None = None
    mrr_at_k: float | None = None
    strong_hit_rate_at_k: float | None = None
    strong_recall_at_k: float | None = None
    strong_mrr_at_k: float | None = None
    strong_ndcg_at_k: float | None = None
    to_cart_mrr_at_k: float | None = None
    to_cart_ndcg_at_k: float | None = None
    coverage_at_k: float | None = None
    popularity_bias_at_k: float | None = None
    fallback_share_at_k: float | None = None
    fallback_category_type_share_at_k: float | None = None
    fallback_category_share_at_k: float | None = None
    fallback_type_share_at_k: float | None = None
    fallback_brand_share_at_k: float | None = None
    fallback_global_share_at_k: float | None = None
    fallback_hit_rate_at_k: float | None = None
    fallback_recall_at_k: float | None = None
    fallback_to_cart_hit_rate_at_k: float | None = None
    fallback_to_cart_recall_at_k: float | None = None
    view_hit_rate_at_k: float | None = None
    view_recall_at_k: float | None = None
    click_hit_rate_at_k: float | None = None
    click_recall_at_k: float | None = None
    favorite_hit_rate_at_k: float | None = None
    favorite_recall_at_k: float | None = None
    to_cart_hit_rate_at_k: float | None = None
    to_cart_recall_at_k: float | None = None
    evaluated_items: int = 0
    recommended_items: int = 0
    ground_truth_pairs: int = 0
    all_evaluated_items: int = 0
    ranking_evaluated_items: int = 0
    view_only_ground_truth_pairs: int = 0
    ranking_ground_truth_pairs: int = 0


def _collect_if_lazy(frame: FrameLike) -> pl.DataFrame:
    if isinstance(frame, pl.LazyFrame):
        return frame.collect()
    return frame


def _safe_divide(numerator: float, denominator: float) -> float:
    if denominator == 0.0:
        return 0.0
    return numerator / denominator


def _optional_float(value: Any) -> float | None:
    """Convert numeric scalar values to float for type checkers and runtime safety."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float | str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _mean_or_none(frame: pl.DataFrame, column: str) -> float | None:
    if frame.is_empty():
        return None
    return _optional_float(frame.select(pl.col(column).mean()).item())


def _popularity_bias(
    top_recommendations: pl.DataFrame,
    context: Mapping[str, Any] | None,
) -> float | None:
    if not context:
        return None

    item_popularity = context.get("item_popularity")
    if item_popularity is None:
        return None

    popularity_column = str(context.get("popularity_column", "events_count"))
    popularity_frame = _collect_if_lazy(item_popularity)
    validate_item_popularity(popularity_frame)

    if popularity_column not in popularity_frame.columns:
        raise ValueError(f"popularity column is missing: {popularity_column}")

    if popularity_frame.is_empty() or top_recommendations.is_empty():
        return None

    max_popularity = _optional_float(popularity_frame[popularity_column].max())
    if max_popularity is None or max_popularity <= 0.0:
        return None

    joined = (
        top_recommendations.join(
            popularity_frame.select(
                "item_id",
                pl.col(popularity_column).cast(pl.Float64).alias("__candidate_popularity"),
            ).rename({"item_id": "similar_item_id"}),
            on="similar_item_id",
            how="left",
        )
        .filter(pl.col("__candidate_popularity").is_not_null())
    )

    if joined.is_empty():
        return None

    mean_candidate_popularity = _optional_float(joined["__candidate_popularity"].mean())
    if mean_candidate_popularity is None:
        return None

    return mean_candidate_popularity / max_popularity


def _fallback_source_filter() -> pl.Expr:
    return pl.col("source").is_null() | (pl.col("source") != "behavioral")


def _fallback_shares(top_recommendations: pl.DataFrame) -> dict[str, float | None]:
    share_values: dict[str, float | None] = {
        metric_name: None for metric_name in FALLBACK_LAYER_SOURCES.values()
    }
    share_values["fallback_share_at_k"] = None

    if top_recommendations.is_empty():
        return share_values

    total_rows = float(top_recommendations.height)
    counts = {
        source: count
        for source, count in top_recommendations.group_by("source")
        .agg(pl.len().alias("__count"))
        .iter_rows()
    }
    fallback_rows = sum(
        count for source, count in counts.items() if source != "behavioral"
    )
    share_values["fallback_share_at_k"] = fallback_rows / total_rows
    for source, metric_name in FALLBACK_LAYER_SOURCES.items():
        share_values[metric_name] = float(counts.get(source, 0)) / total_rows
    return share_values


def _truth_totals(ground_truth: pl.DataFrame) -> pl.DataFrame:
    return ground_truth.group_by("item_id").agg(
        pl.col("relevance").cast(pl.Float64).sum().alias("__total_relevance"),
        *[
            (pl.col(f"{action_type}_count").cast(pl.Float64) > 0.0)
            .sum()
            .alias(f"__{action_type}_truth_count")
            for action_type in ACTION_TYPES
        ],
    )


def _filter_ranking_ground_truth(
    ground_truth: pl.DataFrame,
    *,
    relevant_action_types: Sequence[str],
    min_relevance: float | None,
) -> pl.DataFrame:
    """Keep only ground-truth pairs eligible for the primary ranking metrics."""

    if ground_truth.is_empty():
        return ground_truth

    relevant_actions = tuple(str(action_type) for action_type in relevant_action_types)
    action_filter = (
        pl.col("target_action_type").is_in(relevant_actions)
        if relevant_actions
        else pl.lit(False)
    )
    relevance_filter = pl.lit(False)
    if min_relevance is not None:
        if min_relevance < 0.0:
            raise ValueError("min_ranking_relevance must be >= 0")
        relevance_filter = (
            (pl.col("target_action_type") != "view")
            & (pl.col("relevance").cast(pl.Float64) >= float(min_relevance))
        )

    return ground_truth.filter(action_filter | relevance_filter)


def _view_only_ground_truth_pair_count(ground_truth: pl.DataFrame) -> int:
    if ground_truth.is_empty():
        return 0
    return ground_truth.filter(
        (pl.col("view_count").cast(pl.Float64) > 0.0)
        & (pl.col("click_count").cast(pl.Float64) <= 0.0)
        & (pl.col("favorite_count").cast(pl.Float64) <= 0.0)
        & (pl.col("to_cart_count").cast(pl.Float64) <= 0.0)
    ).height


def _recommendation_hits(
    top_recommendations: pl.DataFrame,
    ground_truth: pl.DataFrame,
) -> pl.DataFrame:
    if top_recommendations.is_empty() or ground_truth.is_empty():
        return top_recommendations.head(0).join(
            ground_truth.head(0),
            left_on=["item_id", "similar_item_id"],
            right_on=["item_id", "relevant_item_id"],
            how="inner",
        )

    return top_recommendations.join(
        ground_truth.with_columns(pl.col("relevance").cast(pl.Float64)),
        left_on=["item_id", "similar_item_id"],
        right_on=["item_id", "relevant_item_id"],
        how="inner",
    )


def _ideal_dcg_by_item(ground_truth: pl.DataFrame, top_k: int) -> pl.DataFrame:
    return (
        ground_truth.with_columns(pl.col("relevance").cast(pl.Float64))
        .sort(["item_id", "relevance"], descending=[False, True])
        .with_columns(pl.col("relevance").cum_count().over("item_id").alias("__ideal_rank"))
        .filter(pl.col("__ideal_rank") <= top_k)
        .with_columns(
            (
                pl.col("relevance")
                / (pl.col("__ideal_rank").cast(pl.Float64) + 1.0).log(2)
            ).alias("__ideal_dcg_component")
        )
        .group_by("item_id")
        .agg(pl.col("__ideal_dcg_component").sum().alias("__ideal_dcg"))
    )


def _empty_ranking_metrics() -> dict[str, float | None | int]:
    metrics: dict[str, float | None | int] = {
        "hit_rate_at_k": None,
        "recall_at_k": None,
        "ndcg_at_k": None,
        "mrr_at_k": None,
        "coverage_at_k": 0.0,
        "evaluated_items": 0,
        "recommended_items": 0,
    }
    for action_type in ACTION_TYPES:
        metrics[f"{action_type}_hit_rate_at_k"] = None
        metrics[f"{action_type}_recall_at_k"] = None
    return metrics


def _ranking_metrics(
    *,
    truth_totals: pl.DataFrame,
    top_recommendations: pl.DataFrame,
    hits: pl.DataFrame,
    ground_truth: pl.DataFrame,
    top_k: int,
) -> dict[str, float | None | int]:
    if truth_totals.is_empty():
        return _empty_ranking_metrics()

    evaluated_items = truth_totals.height
    recommended_items = (
        top_recommendations.select("item_id")
        .unique()
        .join(truth_totals.select("item_id"), on="item_id", how="inner")
        .height
        if not top_recommendations.is_empty()
        else 0
    )

    if hits.is_empty():
        per_item = truth_totals.join(
            _ideal_dcg_by_item(ground_truth, top_k),
            on="item_id",
            how="left",
        ).with_columns(
            pl.lit(0.0).alias("__hit"),
            pl.lit(0.0).alias("__recall"),
            pl.lit(0.0).alias("__mrr"),
            pl.lit(0.0).alias("__ndcg"),
            *[pl.lit(0).alias(f"__{action_type}_hits") for action_type in ACTION_TYPES],
        )
    else:
        hit_by_item = (
            hits.with_columns(
                (
                    pl.col("relevance").cast(pl.Float64)
                    / (pl.col("rank").cast(pl.Float64) + 1.0).log(2)
                ).alias("__dcg_component")
            )
            .group_by("item_id")
            .agg(
                pl.len().alias("__hit_count"),
                pl.col("relevance").cast(pl.Float64).sum().alias("__hit_relevance"),
                pl.col("rank").min().alias("__first_hit_rank"),
                pl.col("__dcg_component").sum().alias("__dcg"),
                *[
                    (pl.col(f"{action_type}_count").cast(pl.Float64) > 0.0)
                    .sum()
                    .alias(f"__{action_type}_hits")
                    for action_type in ACTION_TYPES
                ],
            )
        )
        per_item = (
            truth_totals.join(hit_by_item, on="item_id", how="left")
            .join(_ideal_dcg_by_item(ground_truth, top_k), on="item_id", how="left")
            .with_columns(
                pl.col("__hit_count").fill_null(0),
                pl.col("__hit_relevance").fill_null(0.0),
                pl.col("__first_hit_rank").fill_null(0),
                pl.col("__dcg").fill_null(0.0),
                pl.col("__ideal_dcg").fill_null(0.0),
                *[
                    pl.col(f"__{action_type}_hits").fill_null(0)
                    for action_type in ACTION_TYPES
                ],
            )
            .with_columns(
                (pl.col("__hit_count") > 0).cast(pl.Float64).alias("__hit"),
                pl.when(pl.col("__total_relevance") != 0.0)
                .then(pl.col("__hit_relevance") / pl.col("__total_relevance"))
                .otherwise(0.0)
                .alias("__recall"),
                pl.when(pl.col("__first_hit_rank") > 0)
                .then(1.0 / pl.col("__first_hit_rank").cast(pl.Float64))
                .otherwise(0.0)
                .alias("__mrr"),
                pl.when(pl.col("__ideal_dcg") != 0.0)
                .then(pl.col("__dcg") / pl.col("__ideal_dcg"))
                .otherwise(0.0)
                .alias("__ndcg"),
            )
        )

    metrics: dict[str, float | None | int] = {
        "hit_rate_at_k": _mean_or_none(per_item, "__hit"),
        "recall_at_k": _mean_or_none(per_item, "__recall"),
        "ndcg_at_k": _mean_or_none(per_item, "__ndcg"),
        "mrr_at_k": _mean_or_none(per_item, "__mrr"),
        "coverage_at_k": _safe_divide(float(recommended_items), float(evaluated_items)),
        "evaluated_items": evaluated_items,
        "recommended_items": recommended_items,
    }

    for action_type in ACTION_TYPES:
        action_frame = per_item.filter(pl.col(f"__{action_type}_truth_count") > 0)
        if action_frame.is_empty():
            metrics[f"{action_type}_hit_rate_at_k"] = None
            metrics[f"{action_type}_recall_at_k"] = None
            continue
        action_frame = action_frame.with_columns(
            (pl.col(f"__{action_type}_hits") > 0).cast(pl.Float64).alias("__action_hit"),
            (
                pl.col(f"__{action_type}_hits").cast(pl.Float64)
                / pl.col(f"__{action_type}_truth_count").cast(pl.Float64)
            ).alias("__action_recall"),
        )
        metrics[f"{action_type}_hit_rate_at_k"] = _mean_or_none(
            action_frame,
            "__action_hit",
        )
        metrics[f"{action_type}_recall_at_k"] = _mean_or_none(
            action_frame,
            "__action_recall",
        )

    return metrics


def _fallback_quality_metrics(
    *,
    truth_totals: pl.DataFrame,
    hits: pl.DataFrame,
    to_cart_truth_totals: pl.DataFrame,
    to_cart_hits: pl.DataFrame,
) -> dict[str, float | None]:
    fallback_hits = hits.filter(_fallback_source_filter()) if not hits.is_empty() else hits
    if truth_totals.is_empty():
        fallback_hit_rate = None
        fallback_recall = None
    elif fallback_hits.is_empty():
        per_item = truth_totals.with_columns(
            pl.lit(0.0).alias("__fallback_hit"),
            pl.lit(0.0).alias("__fallback_recall"),
        )
        fallback_hit_rate = _mean_or_none(per_item, "__fallback_hit")
        fallback_recall = _mean_or_none(per_item, "__fallback_recall")
    else:
        fallback_by_item = fallback_hits.group_by("item_id").agg(
            pl.len().alias("__fallback_hit_count"),
            pl.col("relevance").cast(pl.Float64).sum().alias("__fallback_relevance"),
        )
        per_item = (
            truth_totals.join(fallback_by_item, on="item_id", how="left")
            .with_columns(
                pl.col("__fallback_hit_count").fill_null(0),
                pl.col("__fallback_relevance").fill_null(0.0),
            )
            .with_columns(
                (pl.col("__fallback_hit_count") > 0)
                .cast(pl.Float64)
                .alias("__fallback_hit"),
                pl.when(pl.col("__total_relevance") != 0.0)
                .then(pl.col("__fallback_relevance") / pl.col("__total_relevance"))
                .otherwise(0.0)
                .alias("__fallback_recall"),
            )
        )
        fallback_hit_rate = _mean_or_none(per_item, "__fallback_hit")
        fallback_recall = _mean_or_none(per_item, "__fallback_recall")

    fallback_to_cart_hits = (
        to_cart_hits.filter(_fallback_source_filter()) if not to_cart_hits.is_empty() else to_cart_hits
    )
    if to_cart_truth_totals.is_empty():
        fallback_to_cart_hit_rate = None
        fallback_to_cart_recall = None
    elif fallback_to_cart_hits.is_empty():
        to_cart_frame = to_cart_truth_totals.with_columns(
            pl.lit(0.0).alias("__fallback_to_cart_hit"),
            pl.lit(0.0).alias("__fallback_to_cart_recall"),
        )
        fallback_to_cart_hit_rate = _mean_or_none(to_cart_frame, "__fallback_to_cart_hit")
        fallback_to_cart_recall = _mean_or_none(to_cart_frame, "__fallback_to_cart_recall")
    else:
        fallback_to_cart_by_item = (
            fallback_to_cart_hits.unique(["item_id", "similar_item_id"])
            .group_by("item_id")
            .agg(pl.len().alias("__fallback_to_cart_hits"))
        )
        to_cart_frame = (
            to_cart_truth_totals.join(fallback_to_cart_by_item, on="item_id", how="left")
            .with_columns(pl.col("__fallback_to_cart_hits").fill_null(0))
            .with_columns(
                (pl.col("__fallback_to_cart_hits") > 0)
                .cast(pl.Float64)
                .alias("__fallback_to_cart_hit"),
                (
                    pl.col("__fallback_to_cart_hits").cast(pl.Float64)
                    / pl.col("__to_cart_truth_count").cast(pl.Float64)
                ).alias("__fallback_to_cart_recall"),
            )
        )
        fallback_to_cart_hit_rate = _mean_or_none(to_cart_frame, "__fallback_to_cart_hit")
        fallback_to_cart_recall = _mean_or_none(to_cart_frame, "__fallback_to_cart_recall")

    return {
        "fallback_hit_rate_at_k": fallback_hit_rate,
        "fallback_recall_at_k": fallback_recall,
        "fallback_to_cart_hit_rate_at_k": fallback_to_cart_hit_rate,
        "fallback_to_cart_recall_at_k": fallback_to_cart_recall,
    }


def compute_offline_metrics(
    recommendations: FrameLike,
    ground_truth: FrameLike,
    *,
    top_k: int,
    context: dict[str, Any] | None = None,
    ranking_relevant_action_types: Sequence[str] | None = None,
    min_ranking_relevance: float | None = DEFAULT_MIN_RANKING_RELEVANCE,
) -> OfflineMetrics:
    """Compute offline ranking metrics for one evaluation slice."""

    if top_k <= 0:
        raise ValueError("top_k must be a positive integer")

    validate_recommendations(recommendations)
    validate_ground_truth(ground_truth)

    recommendations_frame = _collect_if_lazy(recommendations).select(RECOMMENDATION_METRIC_COLUMNS)
    ground_truth_frame = _collect_if_lazy(ground_truth).select(GROUND_TRUTH_METRIC_COLUMNS)
    top_recommendations = recommendations_frame.filter(pl.col("rank") <= top_k)
    fallback_shares = _fallback_shares(top_recommendations)
    popularity_bias = _popularity_bias(top_recommendations, context)
    if ranking_relevant_action_types is None:
        relevant_action_types = DEFAULT_RANKING_RELEVANT_ACTION_TYPES
    elif isinstance(ranking_relevant_action_types, str):
        relevant_action_types = (ranking_relevant_action_types,)
    else:
        relevant_action_types = tuple(ranking_relevant_action_types)
    resolved_min_relevance = (
        None if min_ranking_relevance is None else float(min_ranking_relevance)
    )

    if ground_truth_frame.is_empty():
        return OfflineMetrics(
            fallback_share_at_k=fallback_shares["fallback_share_at_k"],
            fallback_category_type_share_at_k=fallback_shares[
                "fallback_category_type_share_at_k"
            ],
            fallback_category_share_at_k=fallback_shares[
                "fallback_category_share_at_k"
            ],
            fallback_type_share_at_k=fallback_shares[
                "fallback_type_share_at_k"
            ],
            fallback_brand_share_at_k=fallback_shares[
                "fallback_brand_share_at_k"
            ],
            fallback_global_share_at_k=fallback_shares[
                "fallback_global_share_at_k"
            ],
            popularity_bias_at_k=popularity_bias,
        )

    ranking_ground_truth = _filter_ranking_ground_truth(
        ground_truth_frame,
        relevant_action_types=relevant_action_types,
        min_relevance=resolved_min_relevance,
    )
    to_cart_ground_truth = ground_truth_frame.filter(
        pl.col("to_cart_count").cast(pl.Float64) > 0.0
    )

    full_truth_totals = _truth_totals(ground_truth_frame)
    ranking_truth_totals = _truth_totals(ranking_ground_truth)
    to_cart_truth_totals = _truth_totals(to_cart_ground_truth)
    full_hits = _recommendation_hits(top_recommendations, ground_truth_frame)
    ranking_hits = _recommendation_hits(top_recommendations, ranking_ground_truth)
    to_cart_hits = _recommendation_hits(top_recommendations, to_cart_ground_truth)
    ranking_metrics = _ranking_metrics(
        truth_totals=ranking_truth_totals,
        top_recommendations=top_recommendations,
        hits=ranking_hits,
        ground_truth=ranking_ground_truth,
        top_k=top_k,
    )
    action_metrics = _ranking_metrics(
        truth_totals=full_truth_totals,
        top_recommendations=top_recommendations,
        hits=full_hits,
        ground_truth=ground_truth_frame,
        top_k=top_k,
    )
    to_cart_metrics = _ranking_metrics(
        truth_totals=to_cart_truth_totals,
        top_recommendations=top_recommendations,
        hits=to_cart_hits,
        ground_truth=to_cart_ground_truth,
        top_k=top_k,
    )
    fallback_quality = _fallback_quality_metrics(
        truth_totals=ranking_truth_totals,
        hits=ranking_hits,
        to_cart_truth_totals=to_cart_truth_totals,
        to_cart_hits=to_cart_hits,
    )
    ranking_evaluated_items = int(ranking_metrics["evaluated_items"] or 0)

    return OfflineMetrics(
        hit_rate_at_k=ranking_metrics["hit_rate_at_k"],
        recall_at_k=ranking_metrics["recall_at_k"],
        ndcg_at_k=ranking_metrics["ndcg_at_k"],
        mrr_at_k=ranking_metrics["mrr_at_k"],
        strong_hit_rate_at_k=ranking_metrics["hit_rate_at_k"],
        strong_recall_at_k=ranking_metrics["recall_at_k"],
        strong_mrr_at_k=ranking_metrics["mrr_at_k"],
        strong_ndcg_at_k=ranking_metrics["ndcg_at_k"],
        to_cart_mrr_at_k=to_cart_metrics["mrr_at_k"],
        to_cart_ndcg_at_k=to_cart_metrics["ndcg_at_k"],
        coverage_at_k=ranking_metrics["coverage_at_k"],
        popularity_bias_at_k=popularity_bias,
        fallback_share_at_k=fallback_shares["fallback_share_at_k"],
        fallback_category_type_share_at_k=fallback_shares[
            "fallback_category_type_share_at_k"
        ],
        fallback_category_share_at_k=fallback_shares[
            "fallback_category_share_at_k"
        ],
        fallback_type_share_at_k=fallback_shares["fallback_type_share_at_k"],
        fallback_brand_share_at_k=fallback_shares["fallback_brand_share_at_k"],
        fallback_global_share_at_k=fallback_shares["fallback_global_share_at_k"],
        fallback_hit_rate_at_k=fallback_quality["fallback_hit_rate_at_k"],
        fallback_recall_at_k=fallback_quality["fallback_recall_at_k"],
        fallback_to_cart_hit_rate_at_k=fallback_quality[
            "fallback_to_cart_hit_rate_at_k"
        ],
        fallback_to_cart_recall_at_k=fallback_quality[
            "fallback_to_cart_recall_at_k"
        ],
        view_hit_rate_at_k=action_metrics["view_hit_rate_at_k"],
        view_recall_at_k=action_metrics["view_recall_at_k"],
        click_hit_rate_at_k=action_metrics["click_hit_rate_at_k"],
        click_recall_at_k=action_metrics["click_recall_at_k"],
        favorite_hit_rate_at_k=action_metrics["favorite_hit_rate_at_k"],
        favorite_recall_at_k=action_metrics["favorite_recall_at_k"],
        to_cart_hit_rate_at_k=action_metrics["to_cart_hit_rate_at_k"],
        to_cart_recall_at_k=action_metrics["to_cart_recall_at_k"],
        evaluated_items=ranking_evaluated_items,
        recommended_items=int(ranking_metrics["recommended_items"] or 0),
        ground_truth_pairs=ground_truth_frame.height,
        all_evaluated_items=full_truth_totals.height,
        ranking_evaluated_items=ranking_evaluated_items,
        view_only_ground_truth_pairs=_view_only_ground_truth_pair_count(ground_truth_frame),
        ranking_ground_truth_pairs=ranking_ground_truth.height,
    )
