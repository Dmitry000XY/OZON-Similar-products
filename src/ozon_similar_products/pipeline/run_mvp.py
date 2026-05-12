"""MVP pipeline runner."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from ozon_similar_products.config import PROJECT_ROOT, load_yaml_config
from ozon_similar_products.data import load_configs, load_events, schemas
from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.features.item_popularity import ItemPopularityBuilder
from ozon_similar_products.output.writers import RecommendationWriter
from ozon_similar_products.preprocessing.build_sessions import SessionBuilder
from ozon_similar_products.preprocessing.clean_events import EventCleaner
from ozon_similar_products.retrieval.aggregate_pairs import PairAggregator
from ozon_similar_products.retrieval.build_pairs import ItemPairBuilder
from ozon_similar_products.retrieval.scoring import CoVisitationScorer
from ozon_similar_products.retrieval.topk import TopKSelector


def _as_mapping(value: Any) -> dict[str, Any]:
    """Return a mutable mapping copy or an empty mapping fallback."""
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _as_path(value: Any, default: str) -> Path:
    """Resolve a project-relative path from config."""
    if isinstance(value, str | Path):
        path_value = Path(value)
    else:
        path_value = Path(default)

    if path_value.is_absolute():
        return path_value
    return (PROJECT_ROOT / path_value).resolve()


def _as_optional_int(value: Any) -> int | None:
    """Parse an optional non-negative integer config value."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Expected integer threshold or null, got bool")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        parsed = int(value)
    else:
        raise TypeError("Expected integer threshold or null")

    if parsed < 0:
        raise ValueError("Expected non-negative integer threshold or null")
    return parsed


def _as_positive_int(value: Any, default: int, parameter_name: str) -> int:
    """Parse a positive integer config value without silently accepting bools."""
    if value is None:
        parsed = default
    elif isinstance(value, bool):
        raise ValueError(f"{parameter_name} must be a positive integer")
    elif isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        parsed = int(value)
    else:
        raise TypeError(f"{parameter_name} must be a positive integer")

    if parsed <= 0:
        raise ValueError(f"{parameter_name} must be a positive integer")
    return parsed


def _as_non_empty_str(value: Any, default: str, parameter_name: str) -> str:
    """Parse a non-empty string config value."""
    if value is None:
        return default
    if not isinstance(value, str):
        raise TypeError(f"{parameter_name} must be a string")
    if not value:
        raise ValueError(f"{parameter_name} must be a non-empty string")
    return value


def _item_action_types(config: Mapping[str, Any]) -> list[str]:
    """Read item action types from config."""
    events_config = _as_mapping(config.get("events", {}))
    action_types = events_config.get("item_action_types", schemas.ITEM_SIGNAL_TYPES)

    if isinstance(action_types, str):
        normalized = [action_types]
    elif isinstance(action_types, Sequence):
        normalized = [str(action_type) for action_type in action_types]
    else:
        raise TypeError("events.item_action_types must be a sequence of action-type strings")

    if not normalized or any(not action_type for action_type in normalized):
        raise ValueError("events.item_action_types must not be empty")
    return normalized


def _parse_iso_date(value: str, parameter_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{parameter_name} must be an ISO date string YYYY-MM-DD") from error


def _window_bounds(train_until_date: str, lookback_days: int) -> tuple[str, str]:
    lookback_days = _as_positive_int(
        value=lookback_days,
        default=30,
        parameter_name="lookback_days",
    )
    window_end = _parse_iso_date(train_until_date, "train_until_date")
    window_start = window_end - timedelta(days=lookback_days - 1)
    return window_start.isoformat(), window_end.isoformat()


def _partition_frame_by_date_column(
    frame: pl.DataFrame,
    date_column: str,
) -> list[tuple[str, pl.DataFrame]]:
    """Split a frame into sorted partitions by a date-like column."""
    if frame.is_empty():
        return []

    partitions = frame.partition_by(date_column, as_dict=True, maintain_order=True)
    daily_frames: list[tuple[str, pl.DataFrame]] = []

    for partition_key, partition_frame in partitions.items():
        if isinstance(partition_key, tuple):
            date_value = partition_key[0]
        else:
            date_value = partition_key
        daily_frames.append((str(date_value), partition_frame))

    daily_frames.sort(key=lambda item: item[0])
    return daily_frames


def _partition_raw_events_by_date(raw_events: pl.DataFrame) -> list[tuple[str, pl.DataFrame]]:
    """Split raw events into date partitions."""
    return _partition_frame_by_date_column(raw_events, "date")


def _concat_daily_frames(
    daily_frames: list[tuple[str, pl.DataFrame]],
    contract_columns: Sequence[str],
) -> pl.DataFrame:
    """Concatenate daily frames or return an empty contract frame."""
    if not daily_frames:
        return empty_contract_frame(contract_columns)
    return pl.concat([frame for _, frame in daily_frames], how="vertical")


def _write_daily_partitions(
    daily_frames: list[tuple[str, pl.DataFrame]],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for partition_date, frame in daily_frames:
        frame.write_parquet(output_dir / f"date={partition_date}.parquet")


def _write_window_artifact(
    frame: pl.DataFrame,
    output_dir: Path,
    window_start: str,
    window_end: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"window_start={window_start}_window_end={window_end}.parquet"
    frame.write_parquet(output_path)
    return output_path


def _action_shares_from_distribution(
    distribution: pl.DataFrame,
) -> dict[str, float] | None:
    if distribution.is_empty():
        return None

    action_shares: dict[str, float] = {}
    for row in distribution.select(["action_type", "event_share"]).to_dicts():
        action_type = row.get("action_type")
        event_share = row.get("event_share")
        if action_type is None or event_share is None:
            continue
        action_shares[str(action_type)] = float(event_share)

    if not action_shares:
        return None
    return action_shares


def _run_id(train_until_date: str, lookback_days: int) -> str:
    return f"run_{train_until_date}_lb{lookback_days}"


def _common_parent(paths: Sequence[Path]) -> Path | None:
    if not paths:
        return None
    try:
        common = os.path.commonpath([str(path.resolve()) for path in paths])
    except ValueError:
        return None
    return Path(common)


def _relative_or_name(path: Path, root: Path) -> Path:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return Path(path.name)


def run_mvp_pipeline(
    train_until_date: str,
    lookback_days: int,
    config_path: str | Path = "configs/baseline.yaml",
) -> None:
    """Run full MVP pipeline over a rolling window.

    Pipeline stages:
    1. load daily raw events;
    2. clean events by day;
    3. build sessions over the whole window;
    4. build item pairs;
    5. aggregate pairs over window;
    6. score pairs;
    7. select top-K;
    8. save detailed recommendations;
    9. save widget output;
    10. update latest snapshot.
    """
    config = load_yaml_config(config_path)
    pipeline_config = _as_mapping(config.get("pipeline", {}))
    artifacts_config = _as_mapping(config.get("artifacts", {}))
    outputs_config = _as_mapping(config.get("outputs", {}))
    topk_config = _as_mapping(config.get("topk", {}))

    window_start, window_end = _window_bounds(train_until_date, lookback_days)
    action_types = _item_action_types(config)

    data_config = load_configs(project_root=PROJECT_ROOT)
    try:
        raw_events = load_events(
            config=data_config,
            use_sample=False,
            start_date=window_start,
            end_date=window_end,
            action_types=action_types,
        )
    except FileNotFoundError:
        raw_events = empty_contract_frame(schemas.RAW_EVENTS_COLUMNS)
    daily_raw_events = _partition_raw_events_by_date(raw_events)

    cleaner = EventCleaner(item_action_types=action_types)
    daily_clean_events = [
        (partition_date, cleaner.transform_day(events))
        for partition_date, events in daily_raw_events
    ]
    events_clean_window = _concat_daily_frames(daily_clean_events, schemas.CLEAN_EVENTS_COLUMNS)

    session_builder = SessionBuilder.from_config(config)
    sessions_window = session_builder.transform_window(
        [events_clean for _, events_clean in daily_clean_events]
    )
    daily_sessions = _partition_frame_by_date_column(sessions_window, "event_date")

    pair_builder = ItemPairBuilder.from_config(config)
    pairs_window = pair_builder.transform_day(sessions_window)
    daily_pairs = _partition_frame_by_date_column(pairs_window, "pair_date")

    pair_aggregates = PairAggregator().aggregate_window(
        daily_pairs=[pairs for _, pairs in daily_pairs],
        window_start=window_start,
        window_end=window_end,
    )

    popularity_builder = ItemPopularityBuilder(item_action_types=action_types)
    item_popularity = popularity_builder.build_item_popularity(events_clean_window)
    action_distribution = popularity_builder.build_action_type_calibration_stats(
        events_clean_window,
        calibration_start=window_start,
        calibration_end=window_end,
    )

    scorer = CoVisitationScorer.from_config(config)
    if scorer.action_shares is None:
        derived_action_shares = _action_shares_from_distribution(action_distribution)
        if derived_action_shares is not None:
            scorer = replace(scorer, action_shares=derived_action_shares)

    if scorer.normalize_by_item_popularity:
        pair_scores = scorer.score(pair_aggregates, item_popularity=item_popularity)
    else:
        pair_scores = scorer.score(pair_aggregates)

    top_k = _as_positive_int(
        value=topk_config.get("top_k", pipeline_config.get("top_k")),
        default=20,
        parameter_name="topk.top_k",
    )
    selector = TopKSelector(
        top_k=top_k,
        source=_as_non_empty_str(
            value=topk_config.get("source"),
            default="behavioral",
            parameter_name="topk.source",
        ),
        min_pair_count=_as_optional_int(topk_config.get("min_pair_count")),
        min_unique_users=_as_optional_int(topk_config.get("min_unique_users")),
        min_unique_sessions=_as_optional_int(topk_config.get("min_unique_sessions")),
    )
    recommendations = selector.select(pair_scores)

    events_clean_dir = artifacts_config.get("events_clean_dir")
    sessions_dir = artifacts_config.get("sessions_dir")
    daily_pairs_dir = artifacts_config.get("daily_pairs_dir")
    pair_aggregates_dir = artifacts_config.get("pair_aggregates_dir")
    item_popularity_dir = artifacts_config.get("item_popularity_dir")
    action_type_distribution_dir = artifacts_config.get("action_type_distribution_dir")

    if isinstance(events_clean_dir, str | Path):
        _write_daily_partitions(daily_clean_events, _as_path(events_clean_dir, "data/processed/events_clean"))
    if isinstance(sessions_dir, str | Path):
        _write_daily_partitions(daily_sessions, _as_path(sessions_dir, "data/processed/sessions"))
    if isinstance(daily_pairs_dir, str | Path):
        _write_daily_partitions(daily_pairs, _as_path(daily_pairs_dir, "data/processed/item_pairs"))
    if isinstance(pair_aggregates_dir, str | Path):
        _write_window_artifact(
            frame=pair_aggregates,
            output_dir=_as_path(pair_aggregates_dir, "data/processed/pair_aggregates"),
            window_start=window_start,
            window_end=window_end,
        )
    if isinstance(item_popularity_dir, str | Path):
        _write_window_artifact(
            frame=item_popularity,
            output_dir=_as_path(item_popularity_dir, "data/processed/item_popularity"),
            window_start=window_start,
            window_end=window_end,
        )
    if isinstance(action_type_distribution_dir, str | Path):
        _write_window_artifact(
            frame=action_distribution,
            output_dir=_as_path(
                action_type_distribution_dir,
                "data/processed/action_type_distribution",
            ),
            window_start=window_start,
            window_end=window_end,
        )

    detailed_dir = outputs_config.get("detailed_recommendations_dir", "outputs/recommendations/detailed")
    widget_dir = outputs_config.get("widget_recommendations_dir", "outputs/recommendations/widget")
    latest_dir = _as_path(outputs_config.get("latest_dir"), "outputs/recommendations/latest")

    detailed_base_dir = _as_path(detailed_dir, "outputs/recommendations/detailed")
    widget_base_dir = _as_path(widget_dir, "outputs/recommendations/widget")
    outputs_root = _common_parent([detailed_base_dir, widget_base_dir]) or latest_dir.parent
    if outputs_root.anchor.lower() != latest_dir.parent.anchor.lower():
        outputs_root = latest_dir.parent

    detailed_subdir = _relative_or_name(detailed_base_dir, outputs_root)
    widget_subdir = _relative_or_name(widget_base_dir, outputs_root)

    run_id = _run_id(train_until_date=train_until_date, lookback_days=lookback_days)
    run_dir = outputs_root / "runs" / run_id
    writer = RecommendationWriter()

    detailed_path = writer.save_detailed(recommendations, run_dir / detailed_subdir)
    widget_path = writer.save_widget_format(recommendations, run_dir / widget_subdir)

    detailed_relative_path = detailed_path.relative_to(run_dir).as_posix()
    widget_relative_path = widget_path.relative_to(run_dir).as_posix()
    manifest = {
        "run_id": run_id,
        "generated_at": datetime.now(UTC),
        "train_until_date": train_until_date,
        "lookback_days": lookback_days,
        "window_start": window_start,
        "window_end": window_end,
        "score_method": scorer.method,
        "top_k": top_k,
        "calibration_used": scorer.action_shares is not None,
        "detailed_recommendations_path": detailed_relative_path,
        "widget_recommendations_path": widget_relative_path,
        "paths": {
            "detailed_recommendations_path": detailed_relative_path,
            "widget_recommendations_path": widget_relative_path,
        },
        "rows": {
            "raw_events": raw_events.height,
            "clean_events": events_clean_window.height,
            "sessions": sessions_window.height,
            "daily_pairs": pairs_window.height,
            "pair_aggregates": pair_aggregates.height,
            "pair_scores": pair_scores.height,
            "recommendations": recommendations.height,
        },
    }
    run_manifest_path = writer.save_manifest(manifest, run_dir)
    writer.update_latest_manifest(run_manifest_path, latest_dir)
