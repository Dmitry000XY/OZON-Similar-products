"""Offline metrics for item-to-item recommendation evaluation."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import polars as pl

from ozon_similar_products.data.validation import validate_item_popularity, validate_recommendations
from ozon_similar_products.evaluation.ground_truth import validate_ground_truth

FrameLike = pl.DataFrame | pl.LazyFrame


@dataclass(frozen=True)
class OfflineMetrics:
    """Container for key offline metrics."""

    hit_rate_at_k: float | None = None
    recall_at_k: float | None = None
    ndcg_at_k: float | None = None
    mrr_at_k: float | None = None
    coverage_at_k: float | None = None
    popularity_bias_at_k: float | None = None
    fallback_share_at_k: float | None = None
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


def _collect_if_lazy(frame: FrameLike) -> pl.DataFrame:
    if isinstance(frame, pl.LazyFrame):
        return frame.collect()
    return frame


def _safe_divide(numerator: float, denominator: float) -> float:
    if denominator == 0.0:
        return 0.0
    return numerator / denominator


def _dcg(weighted_hits: list[tuple[int, float]]) -> float:
    return sum(relevance / math.log2(rank + 1.0) for rank, relevance in weighted_hits if rank > 0)


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


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


def _build_truth_by_item(
    ground_truth: pl.DataFrame,
) -> dict[Any, list[dict[str, Any]]]:
    truth_by_item: dict[Any, list[dict[str, Any]]] = {}
    for row in ground_truth.iter_rows(named=True):
        item_id = row["item_id"]
        truth_by_item.setdefault(item_id, []).append(row)
    return truth_by_item


def _build_recommendations_by_item(
    recommendations: pl.DataFrame,
    top_k: int,
) -> dict[Any, list[dict[str, Any]]]:
    recommendations_by_item: dict[Any, list[dict[str, Any]]] = {}
    ranked = recommendations.filter(pl.col("rank") <= top_k).sort(
        ["item_id", "rank", "score", "similar_item_id"], descending=[False, False, True, False]
    )
    for row in ranked.iter_rows(named=True):
        item_id = row["item_id"]
        recommendations_by_item.setdefault(item_id, []).append(row)
    return recommendations_by_item


def _action_relevant_items(
    truth_rows: list[dict[str, Any]],
    action_type: str,
) -> set[Any]:
    count_column = f"{action_type}_count"
    return {
        row["relevant_item_id"]
        for row in truth_rows
        if float(row.get(count_column) or 0.0) > 0.0
    }


def _fallback_share(recommendations: pl.DataFrame, top_k: int) -> float | None:
    if recommendations.is_empty():
        return None

    top_recommendations = recommendations.filter(pl.col("rank") <= top_k)
    if top_recommendations.is_empty():
        return None

    fallback_rows = top_recommendations.filter(pl.col("source") != "behavioral").height
    return fallback_rows / top_recommendations.height


def _popularity_bias(
    recommendations: pl.DataFrame,
    top_k: int,
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

    if popularity_frame.is_empty() or recommendations.is_empty():
        return None

    max_popularity = _optional_float(popularity_frame[popularity_column].max())
    if max_popularity is None or max_popularity <= 0.0:
        return None

    joined = (
        recommendations.filter(pl.col("rank") <= top_k)
        .join(
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


def compute_offline_metrics(
    recommendations: FrameLike,
    ground_truth: FrameLike,
    *,
    top_k: int,
    context: dict[str, Any] | None = None,
) -> OfflineMetrics:
    """Compute offline ranking metrics for one evaluation slice."""

    if top_k <= 0:
        raise ValueError("top_k must be a positive integer")

    validate_recommendations(recommendations)
    validate_ground_truth(ground_truth)

    recommendations_frame = _collect_if_lazy(recommendations)
    ground_truth_frame = _collect_if_lazy(ground_truth)

    if ground_truth_frame.is_empty():
        return OfflineMetrics(
            fallback_share_at_k=_fallback_share(recommendations_frame, top_k),
            popularity_bias_at_k=_popularity_bias(recommendations_frame, top_k, context),
        )

    truth_by_item = _build_truth_by_item(ground_truth_frame)
    recommendations_by_item = _build_recommendations_by_item(
        recommendations_frame,
        top_k=top_k,
    )

    hit_values: list[float] = []
    recall_values: list[float] = []
    mrr_values: list[float] = []
    ndcg_values: list[float] = []
    action_hit_values: dict[str, list[float]] = {
        "view": [],
        "click": [],
        "favorite": [],
        "to_cart": [],
    }
    action_recall_values: dict[str, list[float]] = {
        "view": [],
        "click": [],
        "favorite": [],
        "to_cart": [],
    }

    for item_id, truth_rows in truth_by_item.items():
        relevance_by_item = {row["relevant_item_id"]: float(row["relevance"]) for row in truth_rows}
        total_relevance = sum(relevance_by_item.values())
        action_items = {
            action_type: _action_relevant_items(truth_rows, action_type)
            for action_type in action_hit_values
        }

        recommendations_for_item = recommendations_by_item.get(item_id, [])

        weighted_hits: list[tuple[int, float]] = []
        action_hits = {action_type: 0 for action_type in action_hit_values}

        for row in recommendations_for_item:
            candidate = row["similar_item_id"]
            rank = int(row["rank"])
            relevance = relevance_by_item.get(candidate)

            if relevance is None:
                continue

            weighted_hits.append((rank, relevance))
            for action_type, relevant_items in action_items.items():
                if candidate in relevant_items:
                    action_hits[action_type] += 1

        hit_values.append(1.0 if weighted_hits else 0.0)
        recall_values.append(
            _safe_divide(
                sum(relevance for _, relevance in weighted_hits),
                total_relevance,
            )
        )

        if weighted_hits:
            first_hit_rank = min(rank for rank, _ in weighted_hits)
            mrr_values.append(1.0 / first_hit_rank)
        else:
            mrr_values.append(0.0)

        dcg = _dcg(weighted_hits)
        ideal_relevances = sorted(relevance_by_item.values(), reverse=True)[:top_k]
        ideal_dcg = _dcg(
            [(rank, relevance) for rank, relevance in enumerate(ideal_relevances, start=1)]
        )
        ndcg_values.append(_safe_divide(dcg, ideal_dcg))

        for action_type, relevant_items in action_items.items():
            if not relevant_items:
                continue
            action_hit_values[action_type].append(1.0 if action_hits[action_type] > 0 else 0.0)
            action_recall_values[action_type].append(
                _safe_divide(float(action_hits[action_type]), float(len(relevant_items)))
            )

    evaluated_items = len(truth_by_item)
    recommended_items = len(set(recommendations_by_item) & set(truth_by_item))

    return OfflineMetrics(
        hit_rate_at_k=_mean(hit_values),
        recall_at_k=_mean(recall_values),
        ndcg_at_k=_mean(ndcg_values),
        mrr_at_k=_mean(mrr_values),
        coverage_at_k=_safe_divide(float(recommended_items), float(evaluated_items)),
        popularity_bias_at_k=_popularity_bias(recommendations_frame, top_k, context),
        fallback_share_at_k=_fallback_share(recommendations_frame, top_k),
        view_hit_rate_at_k=_mean(action_hit_values["view"]) if action_hit_values["view"] else None,
        view_recall_at_k=_mean(action_recall_values["view"])
        if action_recall_values["view"]
        else None,
        click_hit_rate_at_k=_mean(action_hit_values["click"])
        if action_hit_values["click"]
        else None,
        click_recall_at_k=_mean(action_recall_values["click"])
        if action_recall_values["click"]
        else None,
        favorite_hit_rate_at_k=_mean(action_hit_values["favorite"])
        if action_hit_values["favorite"]
        else None,
        favorite_recall_at_k=_mean(action_recall_values["favorite"])
        if action_recall_values["favorite"]
        else None,
        to_cart_hit_rate_at_k=_mean(action_hit_values["to_cart"])
        if action_hit_values["to_cart"]
        else None,
        to_cart_recall_at_k=_mean(action_recall_values["to_cart"])
        if action_recall_values["to_cart"]
        else None,
        evaluated_items=evaluated_items,
        recommended_items=recommended_items,
        ground_truth_pairs=ground_truth_frame.height,
    )
