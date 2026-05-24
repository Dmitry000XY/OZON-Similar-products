"""MVP pipeline runner."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
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
from ozon_similar_products.retrieval.build_pairs import DailyPairStats, ItemPairBuilder
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


def _as_bool(value: Any, default: bool, parameter_name: str) -> bool:
    """Parse a strict boolean config value."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise TypeError(f"{parameter_name} must be a boolean")


def _item_action_types(config: Mapping[str, Any]) -> list[str]:
    """Read item action types from config."""
    events_config = _as_mapping(config.get("events", {}))
    action_types = events_config.get("item_action_types", schemas.ITEM_SIGNAL_TYPES)

    if isinstance(action_types, str):
        normalized = [action_types]
    elif isinstance(action_types, Sequence):
        normalized = list(action_types)
    else:
        raise TypeError("events.item_action_types must be a string or sequence of action-type strings")

    if not normalized:
        raise ValueError("events.item_action_types must not be empty")

    for action_type in normalized:
        if not isinstance(action_type, str) or not action_type:
            raise ValueError(
                "events.item_action_types values must be non-empty strings"
            )
        if action_type not in schemas.ITEM_SIGNAL_TYPES:
            raise ValueError(f"Unknown action type: {action_type}")

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


def _date_range_strings(window_start: str, window_end: str) -> list[str]:
    """Return inclusive ISO date strings between two window boundaries."""
    start = _parse_iso_date(window_start, "window_start")
    end = _parse_iso_date(window_end, "window_end")
    if start > end:
        raise ValueError("window_start must be less than or equal to window_end")

    return [
        (start + timedelta(days=offset)).isoformat()
        for offset in range((end - start).days + 1)
    ]


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


def _scan_parquet_paths_or_empty_frame(
        paths: Sequence[Path],
        contract_columns: Sequence[str],
) -> pl.DataFrame | pl.LazyFrame:
    """Scan parquet paths lazily or return an empty eager contract frame."""
    if not paths:
        return empty_contract_frame(contract_columns)
    return pl.scan_parquet([path.as_posix() for path in paths])


def _load_clean_and_write_daily_events(
        *,
        data_config: dict[str, Any],
        cleaner: EventCleaner,
        action_types: Sequence[str],
        window_start: str,
        window_end: str,
        output_dir: Path,
        allow_empty_input: bool,
        logger: logging.Logger,
) -> tuple[list[Path], int, int]:
    """Load raw events day by day, clean each day and write clean-event artifacts.

    This avoids materializing the whole raw-events window in memory. Missing
    dates are skipped, but if the whole window is missing and empty input is not
    allowed, the function fails with a clear error.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    clean_event_paths: list[Path] = []
    raw_events_rows = 0
    clean_events_rows = 0
    last_missing_error: FileNotFoundError | None = None
    missing_dates: list[str] = []

    for partition_date in _date_range_strings(window_start, window_end):
        try:
            raw_day = load_events(
                config=data_config,
                use_sample=False,
                dates=[partition_date],
                action_types=action_types,
            )
        except FileNotFoundError as error:
            last_missing_error = error
            missing_dates.append(partition_date)
            continue

        raw_events_rows += raw_day.height

        clean_day = cleaner.transform_day(raw_day)
        clean_events_rows += clean_day.height

        output_path = output_dir / f"date={partition_date}.parquet"
        clean_day.write_parquet(output_path)
        clean_event_paths.append(output_path)

    if not clean_event_paths:
        if not allow_empty_input:
            raise FileNotFoundError(
                "Input events were not found for run_mvp_pipeline: "
                f"date_window=[{window_start}..{window_end}], "
                f"action_types={list(action_types)}, "
                f"allow_empty_input={allow_empty_input}. "
                f"Missing dates: {missing_dates}"
            ) from last_missing_error

        logger.warning(
            "[run_mvp_pipeline] missing raw events; continuing with empty input "
            "allow_empty_input=%s",
            allow_empty_input,
        )

    return clean_event_paths, raw_events_rows, clean_events_rows


def _partition_sessions_by_session_start_date(
        sessions: pl.DataFrame,
) -> list[tuple[str, pl.DataFrame]]:
    """Split sessions into partitions by each session's start date.

    This keeps cross-midnight sessions together. A session that starts before
    midnight and continues after midnight should be assigned to the day on which
    the session started.
    """
    if sessions.is_empty():
        return []

    partitions = _partition_frame_by_date_column(
        sessions,
        "session_start_date",
    )

    return [
        (partition_date, partition_frame.select(schemas.SESSIONS_COLUMNS))
        for partition_date, partition_frame in partitions
    ]


@dataclass(frozen=True)
class DailyPairStatsPaths:
    """Paths to compact daily pair-stat artifacts built for a pipeline run."""

    count_paths: list[Path]
    user_key_paths: list[Path]
    session_key_paths: list[Path]
    raw_pair_rows: int


def _write_daily_pair_stats(
        *,
        stats: DailyPairStats,
        partition_date: str,
        output_dir: Path,
) -> tuple[Path, Path, Path]:
    """Write compact daily pair-stat artifacts and return their paths."""
    counts_dir = output_dir / "counts"
    user_keys_dir = output_dir / "user_keys"
    session_keys_dir = output_dir / "session_keys"

    counts_dir.mkdir(parents=True, exist_ok=True)
    user_keys_dir.mkdir(parents=True, exist_ok=True)
    session_keys_dir.mkdir(parents=True, exist_ok=True)

    count_path = counts_dir / f"date={partition_date}.parquet"
    user_key_path = user_keys_dir / f"date={partition_date}.parquet"
    session_key_path = session_keys_dir / f"date={partition_date}.parquet"

    stats.counts.write_parquet(count_path)
    stats.user_keys.write_parquet(user_key_path)
    stats.session_keys.write_parquet(session_key_path)

    return count_path, user_key_path, session_key_path


def _build_and_write_daily_pair_stats(
        *,
        daily_sessions: Sequence[tuple[str, pl.DataFrame]],
        pair_builder: ItemPairBuilder,
        output_dir: Path,
) -> DailyPairStatsPaths:
    """Build compact daily pair stats for each sessions partition and write them."""
    count_paths: list[Path] = []
    user_key_paths: list[Path] = []
    session_key_paths: list[Path] = []
    raw_pair_rows = 0

    output_dir.mkdir(parents=True, exist_ok=True)

    for partition_date, sessions in daily_sessions:
        stats = pair_builder.build_daily_pair_stats(sessions)
        raw_pair_rows += stats.raw_pair_rows

        count_path, user_key_path, session_key_path = _write_daily_pair_stats(
            stats=stats,
            partition_date=partition_date,
            output_dir=output_dir,
        )

        count_paths.append(count_path)
        user_key_paths.append(user_key_path)
        session_key_paths.append(session_key_path)

    return DailyPairStatsPaths(
        count_paths=count_paths,
        user_key_paths=user_key_paths,
        session_key_paths=session_key_paths,
        raw_pair_rows=raw_pair_rows,
    )


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
    4. build compact daily item-pair stats;
    5. aggregate pairs over window;
    6. score pairs;
    7. select top-K;
    8. save detailed recommendations;
    9. save widget output;
    10. update latest snapshot.
    """
    logger = logging.getLogger(__name__)
    run_started = time.perf_counter()

    logger.info("[run_mvp_pipeline] load config=%s", config_path)
    config = load_yaml_config(config_path)
    pipeline_config = _as_mapping(config.get("pipeline", {}))
    artifacts_config = _as_mapping(config.get("artifacts", {}))
    outputs_config = _as_mapping(config.get("outputs", {}))
    topk_config = _as_mapping(config.get("topk", {}))

    window_start, window_end = _window_bounds(train_until_date, lookback_days)
    action_types = _item_action_types(config)
    logger.info(
        "[run_mvp_pipeline] window=%s..%s lookback_days=%s action_types=%s",
        window_start,
        window_end,
        lookback_days,
        action_types,
    )
    allow_empty_input = _as_bool(
        pipeline_config.get("allow_empty_input"),
        default=False,
        parameter_name="pipeline.allow_empty_input",
    )
    allow_empty_latest_update = _as_bool(
        pipeline_config.get("allow_empty_latest_update"),
        default=False,
        parameter_name="pipeline.allow_empty_latest_update",
    )

    data_config = load_configs(project_root=PROJECT_ROOT)
    cleaner = EventCleaner(item_action_types=action_types)
    events_clean_output_dir = _as_path(
        artifacts_config.get("events_clean_dir"),
        "data/processed/events_clean",
    )

    logger.info("[run_mvp_pipeline] load and clean raw events by day")
    clean_event_paths, raw_events_rows, clean_events_rows = _load_clean_and_write_daily_events(
        data_config=data_config,
        cleaner=cleaner,
        action_types=action_types,
        window_start=window_start,
        window_end=window_end,
        output_dir=events_clean_output_dir,
        allow_empty_input=allow_empty_input,
        logger=logger,
    )

    if raw_events_rows == 0:
        logger.warning(
            "[run_mvp_pipeline] raw events empty for window=%s..%s",
            window_start,
            window_end,
        )

    logger.info(
        "[run_mvp_pipeline] raw events loaded rows=%s clean events rows=%s days=%s output_dir=%s",
        raw_events_rows,
        clean_events_rows,
        len(clean_event_paths),
        events_clean_output_dir,
    )

    events_clean_input = _scan_parquet_paths_or_empty_frame(
        clean_event_paths,
        schemas.CLEAN_EVENTS_COLUMNS,
    )

    logger.info("[run_mvp_pipeline] build sessions")
    session_builder = SessionBuilder.from_config(config)
    logger.info("[run_mvp_pipeline] session builder config=%s", session_builder)
    sessions_window = session_builder.transform_window(
        [events_clean_input] if clean_event_paths else []
    )

    sessions_rows = sessions_window.height
    logger.info("[run_mvp_pipeline] sessions window rows=%s", sessions_rows)
    daily_sessions = _partition_frame_by_date_column(sessions_window, "event_date")
    logger.info(
        "[run_mvp_pipeline] sessions rows=%s days=%s",
        sessions_rows,
        len(daily_sessions),
    )

    sessions_dir = artifacts_config.get("sessions_dir")
    if isinstance(sessions_dir, str | Path):
        _write_daily_partitions(
            daily_sessions,
            _as_path(sessions_dir, "data/processed/sessions"),
        )

    logger.info("[run_mvp_pipeline] build compact daily item-pair stats")
    pair_builder = ItemPairBuilder.from_config(config)
    daily_sessions_for_pairs = _partition_sessions_by_session_start_date(sessions_window)
    daily_pairs_output_dir = _as_path(
        artifacts_config.get("daily_pairs_dir"),
        "data/processed/item_pairs",
    )
    daily_pair_stats_paths = _build_and_write_daily_pair_stats(
        daily_sessions=daily_sessions_for_pairs,
        pair_builder=pair_builder,
        output_dir=daily_pairs_output_dir,
    )
    daily_pairs_rows = daily_pair_stats_paths.raw_pair_rows
    logger.info(
        "[run_mvp_pipeline] daily item pairs rows=%s days=%s output_dir=%s",
        daily_pairs_rows,
        len(daily_pair_stats_paths.count_paths),
        daily_pairs_output_dir,
    )

    del daily_sessions
    del daily_sessions_for_pairs
    del sessions_window

    logger.info("[run_mvp_pipeline] aggregate pairs from compact daily stats")
    pair_aggregates = PairAggregator().aggregate_window_from_daily_stats_paths(
        count_paths=daily_pair_stats_paths.count_paths,
        user_key_paths=daily_pair_stats_paths.user_key_paths,
        session_key_paths=daily_pair_stats_paths.session_key_paths,
        window_start=window_start,
        window_end=window_end,
    )
    pair_aggregates_rows = pair_aggregates.height
    logger.info(
        "[run_mvp_pipeline] pair aggregates rows=%s",
        pair_aggregates_rows,
    )

    pair_aggregates_dir = artifacts_config.get("pair_aggregates_dir")
    if isinstance(pair_aggregates_dir, str | Path):
        _write_window_artifact(
            frame=pair_aggregates,
            output_dir=_as_path(pair_aggregates_dir, "data/processed/pair_aggregates"),
            window_start=window_start,
            window_end=window_end,
        )

    logger.info("[run_mvp_pipeline] build item popularity and action distribution")
    popularity_builder = ItemPopularityBuilder(item_action_types=action_types)
    item_popularity = popularity_builder.build_item_popularity(events_clean_input)
    action_distribution = popularity_builder.build_action_type_calibration_stats(
        events_clean_input,
        calibration_start=window_start,
        calibration_end=window_end,
    )
    item_popularity_rows = item_popularity.height
    action_distribution_rows = action_distribution.height
    logger.info(
        "[run_mvp_pipeline] item popularity rows=%s action_distribution rows=%s",
        item_popularity_rows,
        action_distribution_rows,
    )

    item_popularity_dir = artifacts_config.get("item_popularity_dir")
    if isinstance(item_popularity_dir, str | Path):
        _write_window_artifact(
            frame=item_popularity,
            output_dir=_as_path(item_popularity_dir, "data/processed/item_popularity"),
            window_start=window_start,
            window_end=window_end,
        )

    action_type_distribution_dir = artifacts_config.get("action_type_distribution_dir")
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

    logger.info("[run_mvp_pipeline] score pairs")
    scorer = CoVisitationScorer.from_config(config)
    if scorer.action_shares is None:
        derived_action_shares = _action_shares_from_distribution(action_distribution)
        if derived_action_shares is not None:
            scorer = replace(scorer, action_shares=derived_action_shares)

    del action_distribution

    if scorer.normalize_by_item_popularity:
        pair_scores = scorer.score(pair_aggregates, item_popularity=item_popularity)
    else:
        pair_scores = scorer.score(pair_aggregates)

    pair_scores_rows = pair_scores.height
    del pair_aggregates
    del item_popularity

    logger.info(
        "[run_mvp_pipeline] pair scores rows=%s calibration_used=%s",
        pair_scores_rows,
        scorer.action_shares is not None,
    )

    top_k = _as_positive_int(
        value=topk_config.get("top_k", pipeline_config.get("top_k")),
        default=20,
        parameter_name="topk.top_k",
    )
    logger.info("[run_mvp_pipeline] select top_k=%s", top_k)
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
    del pair_scores

    recommendations_rows = recommendations.height
    if recommendations_rows == 0:
        logger.warning("[run_mvp_pipeline] recommendations empty")
    logger.info(
        "[run_mvp_pipeline] recommendations rows=%s",
        recommendations_rows,
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

    logger.info("[run_mvp_pipeline] write outputs run_id=%s", run_id)
    detailed_path = writer.save_detailed(recommendations, run_dir / detailed_subdir)
    widget_path = writer.save_widget_format(recommendations, run_dir / widget_subdir)

    del recommendations

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
            "raw_events": raw_events_rows,
            "clean_events": clean_events_rows,
            "sessions": sessions_rows,
            "daily_pairs": daily_pairs_rows,
            "pair_aggregates": pair_aggregates_rows,
            "pair_scores": pair_scores_rows,
            "recommendations": recommendations_rows,
        },
    }
    run_manifest_path = writer.save_manifest(manifest, run_dir)
    if recommendations_rows > 0 or allow_empty_latest_update:
        writer.update_latest_manifest(run_manifest_path, latest_dir)
    elif recommendations_rows == 0:
        logger.warning(
            "[run_mvp_pipeline] latest manifest not updated (empty recommendations, allow_empty_latest_update=%s)",
            allow_empty_latest_update,
        )

    elapsed_seconds = time.perf_counter() - run_started
    logger.info("[run_mvp_pipeline] finished in %.2fs", elapsed_seconds)
