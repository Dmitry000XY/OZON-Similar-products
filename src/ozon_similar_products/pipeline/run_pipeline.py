"""Recommendation pipeline runner."""

from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from ozon_similar_products.business.fallback import (
    FALLBACK_SOURCE_LABELS,
    FallbackConfig,
    FallbackLayer,
)
from ozon_similar_products.config import PROJECT_ROOT, get_path_from_config, load_yaml_config
from ozon_similar_products.data import load_configs, load_events, load_products, schemas
from ozon_similar_products.data.frames import empty_contract_frame
from ozon_similar_products.data.partitions import collect_event_parquet_files
from ozon_similar_products.data.readers import find_parquet_payload_dir
from ozon_similar_products.features.item_popularity import ItemPopularityBuilder
from ozon_similar_products.output.writers import RecommendationWriter
from ozon_similar_products.pipeline.artifact_manifest import (
    SCHEMA_VERSION,
    ArtifactManifest,
    fingerprint_payload,
    read_manifest,
    validate_manifest,
    write_manifest,
)
from ozon_similar_products.preprocessing.build_sessions import SessionBuilder
from ozon_similar_products.preprocessing.clean_events import EventCleaner
from ozon_similar_products.retrieval.aggregate_pairs import PairAggregator
from ozon_similar_products.retrieval.build_pairs import DailyPairStats, ItemPairBuilder
from ozon_similar_products.retrieval.scoring import CoVisitationScorer
from ozon_similar_products.retrieval.topk import TopKSelector


@dataclass(frozen=True)
class PipelineRunResult:
    """Materialized output paths and metadata for one recommendation run."""

    run_id: str
    run_dir: Path
    manifest_path: Path
    detailed_recommendations_path: Path
    enriched_recommendations_path: Path
    lookup_recommendations_path: Path
    manifest: dict[str, Any]


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


def _update_strategy(config: Mapping[str, Any]) -> str:
    pipeline_config = _as_mapping(config.get("pipeline", {}))
    strategy = str(pipeline_config.get("update_strategy", "full_retrain"))
    if strategy not in {"full_retrain", "incremental"}:
        raise ValueError("pipeline.update_strategy must be 'full_retrain' or 'incremental'")
    return strategy


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


def _relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _clean_events_manifest_path(output_dir: Path, partition_date: str) -> Path:
    return output_dir / "manifests" / f"date={partition_date}.json"


def _daily_pair_stats_manifest_path(output_dir: Path, partition_date: str) -> Path:
    return output_dir / "manifests" / f"date={partition_date}.json"


def _session_state_manifest_path(output_dir: Path, partition_date: str) -> Path:
    return output_dir / "manifests" / f"date={partition_date}.json"


def _clean_events_fingerprint(
        *,
        config: Mapping[str, Any],
        action_types: Sequence[str],
        partition_date: str,
        raw_input_identity: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    return fingerprint_payload(
        {
            "schema_version": SCHEMA_VERSION,
            "stage": "clean_events",
            "date": partition_date,
            "events": {
                "item_action_types": list(action_types),
                "search_action_type": _as_mapping(config.get("events", {})).get(
                    "search_action_type",
                ),
            },
            "raw_input_identity": [dict(item) for item in (raw_input_identity or [])],
        }
    )


def _session_state_fingerprint(
        *,
        config: Mapping[str, Any],
        action_types: Sequence[str],
        partition_date: str,
        raw_input_identity: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    pipeline_config = _as_mapping(config.get("pipeline", {}))
    return fingerprint_payload(
        {
            "schema_version": SCHEMA_VERSION,
            "stage": "session_state",
            "date": partition_date,
            "clean_events": _clean_events_fingerprint(
                config=config,
                action_types=action_types,
                partition_date=partition_date,
                raw_input_identity=raw_input_identity,
            ),
            "pipeline": {
                "session_timeout_minutes": pipeline_config.get("session_timeout_minutes"),
                "max_items_per_session": pipeline_config.get("max_items_per_session"),
                "session_user_buckets": pipeline_config.get("session_user_buckets"),
            },
        }
    )


def _daily_pair_stats_fingerprint(
        *,
        config: Mapping[str, Any],
        action_types: Sequence[str],
        partition_date: str,
        processed_through_date: str,
        raw_input_identity: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    graph_config = _as_mapping(config.get("graph", {}))
    return fingerprint_payload(
        {
            "schema_version": SCHEMA_VERSION,
            "stage": "daily_pair_stats",
            "date": partition_date,
            "processed_through_date": processed_through_date,
            "session_state": _session_state_fingerprint(
                config=config,
                action_types=action_types,
                partition_date=partition_date,
                raw_input_identity=raw_input_identity,
            ),
            "item_pair_builder": _as_mapping(config.get("item_pair_builder", {})),
            "graph_distance_decay": _as_mapping(graph_config.get("distance_decay", {})),
            "graph_widget_context": _as_mapping(graph_config.get("widget_context", {})),
        }
    )


def _finalize_daily_pair_stat_manifests(
        *,
        count_paths: Sequence[Path],
        daily_pairs_output_dir: Path,
        config: Mapping[str, Any],
        action_types: Sequence[str],
        processed_through_date: str,
        raw_input_identity_by_date: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    """Finalize built pair-stat manifests to the full processed cutoff."""

    for count_path in count_paths:
        partition_date = count_path.stem.removeprefix("date=")
        manifest_path = _daily_pair_stats_manifest_path(
            daily_pairs_output_dir,
            partition_date,
        )
        manifest = read_manifest(manifest_path)
        if manifest is None:
            raise FileNotFoundError(
                "missing daily pair-stat manifest for built artifact "
                f"date={partition_date}: {manifest_path}"
            )

        write_manifest(
            manifest_path,
            ArtifactManifest(
                artifact_type=manifest.artifact_type,
                date=manifest.date,
                fingerprint=_daily_pair_stats_fingerprint(
                    config=config,
                    action_types=action_types,
                    partition_date=manifest.date,
                    processed_through_date=processed_through_date,
                    raw_input_identity=raw_input_identity_by_date.get(manifest.date, []),
                ),
                paths=dict(manifest.paths),
                rows=dict(manifest.rows),
                metadata={
                    **manifest.metadata,
                    "processed_through_date": processed_through_date,
                },
                schema_version=manifest.schema_version,
                created_at=manifest.created_at,
            ),
        )


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


def _raw_event_input_identity(
        *,
        data_config: dict[str, Any],
        partition_date: str,
        action_types: Sequence[str],
) -> list[dict[str, Any]]:
    """Describe the raw parquet files contributing to one event partition."""
    user_actions_base_dir = get_path_from_config(
        config=data_config,
        section="data",
        key="user_actions_dir",
    )
    user_actions_config = data_config["data"]["user_actions"]
    events_dir = find_parquet_payload_dir(
        base_dir=user_actions_base_dir,
        payload_root_names=user_actions_config["payload_root_names"],
        parquet_glob=user_actions_config["parquet_glob"],
    )
    parquet_files = collect_event_parquet_files(
        events_dir=events_dir,
        dates=[partition_date],
        action_types=action_types,
    )

    identity: list[dict[str, Any]] = []
    for parquet_file in sorted(parquet_files):
        stat = parquet_file.stat()
        identity.append(
            {
                "path": parquet_file.relative_to(PROJECT_ROOT).as_posix(),
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return identity


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


def _current_rss_mb() -> float | None:
    """Return current process RSS in MiB when supported."""
    try:
        import os

        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except ImportError:
        pass

    try:
        import resource

        getrusage = getattr(resource, "getrusage", None)
        rusage_self = getattr(resource, "RUSAGE_SELF", None)
        if not callable(getrusage) or rusage_self is None:
            return None

        usage = getrusage(rusage_self)
        rss_kb = getattr(usage, "ru_maxrss", None)
        if rss_kb is None:
            return None
        return float(rss_kb) / 1024
    except (ImportError, AttributeError):
        return None


def _scan_parquet_paths_or_empty_frame(
        paths: Sequence[Path],
        contract_columns: Sequence[str],
) -> pl.DataFrame | pl.LazyFrame:
    """Scan parquet paths lazily or return an empty eager contract frame."""
    if not paths:
        return empty_contract_frame(contract_columns)
    return pl.scan_parquet([path.as_posix() for path in paths])


def _concat_recommendation_parts(parts: Sequence[pl.DataFrame]) -> pl.DataFrame:
    """Concat recommendation chunks while preserving optional diagnostic columns."""
    if not parts:
        return empty_contract_frame(schemas.RECOMMENDATIONS_COLUMNS)

    return pl.concat(parts, how="diagonal_relaxed")


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
                "Input events were not found for run_pipeline: "
                f"date_window=[{window_start}..{window_end}], "
                f"action_types={list(action_types)}, "
                f"allow_empty_input={allow_empty_input}. "
                f"Missing dates: {missing_dates}"
            ) from last_missing_error

        logger.warning(
            "[run_pipeline] missing raw events; continuing with empty input "
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
    widget_count_paths: list[Path]
    user_key_paths: list[Path]
    session_key_paths: list[Path]
    raw_pair_rows: int


@dataclass(frozen=True)
class CleanEventsResult:
    paths: list[Path]
    raw_events_rows: int
    clean_events_rows: int
    reused_days: list[str]
    rebuilt_days: list[str]
    raw_input_identity_by_date: dict[str, list[dict[str, Any]]]


@dataclass(frozen=True)
class PairStatsPlan:
    paths: DailyPairStatsPaths
    sessions_rows: int
    reused_days: list[str]
    invalid_days: list[str]


def _clean_events_path(output_dir: Path, partition_date: str) -> Path:
    return output_dir / f"date={partition_date}.parquet"


def _daily_pair_stats_paths_for_date(output_dir: Path, partition_date: str) -> tuple[Path, Path, Path, Path]:
    return (
        output_dir / "counts" / f"date={partition_date}.parquet",
        output_dir / "widget_counts" / f"date={partition_date}.parquet",
        output_dir / "user_keys" / f"date={partition_date}.parquet",
        output_dir / "session_keys" / f"date={partition_date}.parquet",
    )


def _load_or_build_clean_events(
        *,
        config: Mapping[str, Any],
        data_config: dict[str, Any],
        cleaner: EventCleaner,
        action_types: Sequence[str],
        window_start: str,
        window_end: str,
        output_dir: Path,
        allow_empty_input: bool,
        update_strategy: str,
        logger: logging.Logger,
) -> CleanEventsResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    raw_events_rows = 0
    clean_events_rows = 0
    reused_days: list[str] = []
    rebuilt_days: list[str] = []
    raw_input_identity_by_date: dict[str, list[dict[str, Any]]] = {}
    last_missing_error: FileNotFoundError | None = None
    missing_dates: list[str] = []

    for partition_date in _date_range_strings(window_start, window_end):
        output_path = _clean_events_path(output_dir, partition_date)
        try:
            raw_input_identity = _raw_event_input_identity(
                data_config=data_config,
                partition_date=partition_date,
                action_types=action_types,
            )
        except (KeyError, TypeError):
            raw_input_identity = []
        except FileNotFoundError as error:
            last_missing_error = error
            missing_dates.append(partition_date)
            continue
        raw_input_identity_by_date[partition_date] = raw_input_identity
        fingerprint = _clean_events_fingerprint(
            config=config,
            action_types=action_types,
            partition_date=partition_date,
            raw_input_identity=raw_input_identity,
        )
        status = validate_manifest(
            manifest_path=_clean_events_manifest_path(output_dir, partition_date),
            artifact_root=output_dir,
            artifact_type="clean_events",
            partition_date=partition_date,
            expected_fingerprint=fingerprint,
            required_path_keys=("events",),
        )
        if update_strategy == "incremental" and status.valid and status.manifest is not None:
            paths.append(output_path)
            reused_days.append(partition_date)
            raw_events_rows += int(status.manifest.rows.get("raw_events", 0))
            clean_events_rows += int(status.manifest.rows.get("clean_events", 0))
            continue

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
        clean_day.write_parquet(output_path)
        paths.append(output_path)
        rebuilt_days.append(partition_date)
        write_manifest(
            _clean_events_manifest_path(output_dir, partition_date),
            ArtifactManifest(
                artifact_type="clean_events",
                date=partition_date,
                fingerprint=fingerprint,
                paths={"events": _relative_to_root(output_path, output_dir)},
                rows={"raw_events": raw_day.height, "clean_events": clean_day.height},
                metadata={"raw_input_identity": raw_input_identity},
            ),
        )

    if not paths:
        if not allow_empty_input:
            raise FileNotFoundError(
                "Input events were not found for run_pipeline: "
                f"date_window=[{window_start}..{window_end}], "
                f"action_types={list(action_types)}, "
                f"allow_empty_input={allow_empty_input}. "
                f"Missing dates: {missing_dates}"
            ) from last_missing_error

        logger.warning(
            "[run_pipeline] missing raw events; continuing with empty input "
            "allow_empty_input=%s",
            allow_empty_input,
        )

    return CleanEventsResult(
        paths=paths,
        raw_events_rows=raw_events_rows,
        clean_events_rows=clean_events_rows,
        reused_days=reused_days,
        rebuilt_days=rebuilt_days,
        raw_input_identity_by_date=raw_input_identity_by_date,
    )


def _plan_reusable_daily_pair_stats(
        *,
        config: Mapping[str, Any],
        action_types: Sequence[str],
        partition_dates: Sequence[str],
        daily_pairs_output_dir: Path,
        session_state_output_dir: Path,
        processed_through_date: str,
        raw_input_identity_by_date: Mapping[str, Sequence[Mapping[str, Any]]],
) -> PairStatsPlan:
    count_paths: list[Path] = []
    widget_count_paths: list[Path] = []
    user_key_paths: list[Path] = []
    session_key_paths: list[Path] = []
    raw_pair_rows = 0
    reused_days: list[str] = []
    invalid_days: list[str] = []

    for partition_date in partition_dates:
        raw_input_identity = raw_input_identity_by_date.get(partition_date, [])
        pair_fingerprint = _daily_pair_stats_fingerprint(
            config=config,
            action_types=action_types,
            partition_date=partition_date,
            processed_through_date=processed_through_date,
            raw_input_identity=raw_input_identity,
        )
        pair_status = validate_manifest(
            manifest_path=_daily_pair_stats_manifest_path(daily_pairs_output_dir, partition_date),
            artifact_root=daily_pairs_output_dir,
            artifact_type="daily_pair_stats",
            partition_date=partition_date,
            expected_fingerprint=pair_fingerprint,
            required_path_keys=("counts", "widget_counts", "user_keys", "session_keys"),
        )
        session_status = validate_manifest(
            manifest_path=_session_state_manifest_path(session_state_output_dir, partition_date),
            artifact_root=session_state_output_dir,
            artifact_type="session_state",
            partition_date=partition_date,
            expected_fingerprint=_session_state_fingerprint(
                config=config,
                action_types=action_types,
                partition_date=partition_date,
                raw_input_identity=raw_input_identity,
            ),
            required_path_keys=("active_sessions", "max_session_indices"),
        )
        if (
            not pair_status.valid
            or not session_status.valid
            or pair_status.manifest is None
            or pair_status.manifest.metadata.get("processed_through_date") != processed_through_date
        ):
            invalid_days.append(partition_date)
            continue

        count_path, widget_count_path, user_key_path, session_key_path = _daily_pair_stats_paths_for_date(
            daily_pairs_output_dir,
            partition_date,
        )
        count_paths.append(count_path)
        widget_count_paths.append(widget_count_path)
        user_key_paths.append(user_key_path)
        session_key_paths.append(session_key_path)
        raw_pair_rows += int(pair_status.manifest.rows.get("raw_pair_rows", 0))
        reused_days.append(partition_date)

    sessions_rows = 0
    for partition_date in reused_days:
        session_manifest = read_manifest(
            _session_state_manifest_path(session_state_output_dir, partition_date)
        )
        if session_manifest is not None:
            sessions_rows += int(session_manifest.rows.get("completed_sessions_rows", 0))

    return PairStatsPlan(
        paths=DailyPairStatsPaths(
            count_paths=count_paths,
            widget_count_paths=widget_count_paths,
            user_key_paths=user_key_paths,
            session_key_paths=session_key_paths,
            raw_pair_rows=raw_pair_rows,
        ),
        sessions_rows=sessions_rows,
        reused_days=reused_days,
        invalid_days=invalid_days,
    )


def _with_weighted_count_columns(frame: pl.DataFrame) -> pl.DataFrame:
    expressions = []
    for raw_column, weighted_column in schemas.WEIGHTED_COUNT_BY_RAW_COLUMN.items():
        if weighted_column in frame.columns:
            expressions.append(pl.col(weighted_column).cast(pl.Float64).alias(weighted_column))
        else:
            expressions.append(pl.col(raw_column).cast(pl.Float64).alias(weighted_column))
    return frame.with_columns(expressions)


def _merge_daily_pair_counts(
        existing: pl.DataFrame,
        new: pl.DataFrame,
) -> pl.DataFrame:
    existing = _with_weighted_count_columns(existing)
    new = _with_weighted_count_columns(new)
    merged = pl.concat([existing, new], how="vertical")
    if merged.is_empty():
        return empty_contract_frame(schemas.DAILY_PAIR_COUNTS_COLUMNS)

    return (
        merged.group_by(["pair_date", "item_id", "similar_item_id"])
        .agg(
            pl.col("pair_count").sum().alias("pair_count"),
            pl.col("view_count").sum().alias("view_count"),
            pl.col("click_count").sum().alias("click_count"),
            pl.col("favorite_count").sum().alias("favorite_count"),
            pl.col("to_cart_count").sum().alias("to_cart_count"),
            pl.col("weighted_pair_count").sum().alias("weighted_pair_count"),
            pl.col("weighted_view_count").sum().alias("weighted_view_count"),
            pl.col("weighted_click_count").sum().alias("weighted_click_count"),
            pl.col("weighted_favorite_count").sum().alias("weighted_favorite_count"),
            pl.col("weighted_to_cart_count").sum().alias("weighted_to_cart_count"),
        )
        .select(schemas.DAILY_PAIR_COUNTS_COLUMNS)
        .sort(["pair_date", "item_id", "similar_item_id"])
    )


def _combine_daily_pair_stats(stats_list: Sequence[DailyPairStats]) -> DailyPairStats:
    """Combine compact pair stats from multiple session batches."""
    non_empty_stats = [
        stats for stats in stats_list
        if not stats.counts.is_empty()
    ]

    raw_pair_rows = sum(stats.raw_pair_rows for stats in stats_list)

    if not non_empty_stats:
        return DailyPairStats(
            counts=empty_contract_frame(schemas.DAILY_PAIR_COUNTS_COLUMNS),
            widget_counts=empty_contract_frame(schemas.DAILY_PAIR_WIDGET_COUNTS_COLUMNS),
            user_keys=empty_contract_frame(schemas.DAILY_PAIR_USER_KEYS_COLUMNS),
            session_keys=empty_contract_frame(schemas.DAILY_PAIR_SESSION_KEYS_COLUMNS),
            raw_pair_rows=raw_pair_rows,
        )

    counts = (
        pl.concat(
            [_with_weighted_count_columns(stats.counts) for stats in non_empty_stats],
            how="vertical",
        )
        .group_by(["pair_date", "item_id", "similar_item_id"])
        .agg(
            pl.col("pair_count").sum().alias("pair_count"),
            pl.col("view_count").sum().alias("view_count"),
            pl.col("click_count").sum().alias("click_count"),
            pl.col("favorite_count").sum().alias("favorite_count"),
            pl.col("to_cart_count").sum().alias("to_cart_count"),
            pl.col("weighted_pair_count").sum().alias("weighted_pair_count"),
            pl.col("weighted_view_count").sum().alias("weighted_view_count"),
            pl.col("weighted_click_count").sum().alias("weighted_click_count"),
            pl.col("weighted_favorite_count").sum().alias("weighted_favorite_count"),
            pl.col("weighted_to_cart_count").sum().alias("weighted_to_cart_count"),
        )
        .select(schemas.DAILY_PAIR_COUNTS_COLUMNS)
        .sort(["pair_date", "item_id", "similar_item_id"])
    )

    non_empty_widget_stats = [
        stats for stats in stats_list
        if not stats.widget_counts.is_empty()
    ]
    if non_empty_widget_stats:
        widget_counts = (
            pl.concat(
                [
                    _with_weighted_count_columns(stats.widget_counts)
                    for stats in non_empty_widget_stats
                ],
                how="vertical",
            )
            .group_by(["pair_date", "item_id", "similar_item_id", "target_widget_name"])
            .agg(
                pl.col("pair_count").sum().alias("pair_count"),
                pl.col("view_count").sum().alias("view_count"),
                pl.col("click_count").sum().alias("click_count"),
                pl.col("favorite_count").sum().alias("favorite_count"),
                pl.col("to_cart_count").sum().alias("to_cart_count"),
                pl.col("weighted_pair_count").sum().alias("weighted_pair_count"),
                pl.col("weighted_view_count").sum().alias("weighted_view_count"),
                pl.col("weighted_click_count").sum().alias("weighted_click_count"),
                pl.col("weighted_favorite_count").sum().alias("weighted_favorite_count"),
                pl.col("weighted_to_cart_count").sum().alias("weighted_to_cart_count"),
            )
            .select(schemas.DAILY_PAIR_WIDGET_COUNTS_COLUMNS)
            .sort(["pair_date", "item_id", "similar_item_id", "target_widget_name"])
        )
    else:
        widget_counts = empty_contract_frame(schemas.DAILY_PAIR_WIDGET_COUNTS_COLUMNS)

    user_keys = (
        pl.concat([stats.user_keys for stats in non_empty_stats], how="vertical")
        .select(schemas.DAILY_PAIR_USER_KEYS_COLUMNS)
        .unique()
        .sort(["pair_date", "item_id", "similar_item_id", "user_id"])
    )

    session_keys = (
        pl.concat([stats.session_keys for stats in non_empty_stats], how="vertical")
        .select(schemas.DAILY_PAIR_SESSION_KEYS_COLUMNS)
        .unique()
        .sort(["pair_date", "item_id", "similar_item_id", "user_id", "session_index"])
    )

    return DailyPairStats(
        counts=counts,
        widget_counts=widget_counts,
        user_keys=user_keys,
        session_keys=session_keys,
        raw_pair_rows=raw_pair_rows,
    )


def _merge_daily_pair_widget_counts(
        existing: pl.DataFrame,
        new: pl.DataFrame,
) -> pl.DataFrame:
    existing = _with_weighted_count_columns(existing)
    new = _with_weighted_count_columns(new)
    merged = pl.concat([existing, new], how="vertical")
    if merged.is_empty():
        return empty_contract_frame(schemas.DAILY_PAIR_WIDGET_COUNTS_COLUMNS)

    return (
        merged.group_by(["pair_date", "item_id", "similar_item_id", "target_widget_name"])
        .agg(
            pl.col("pair_count").sum().alias("pair_count"),
            pl.col("view_count").sum().alias("view_count"),
            pl.col("click_count").sum().alias("click_count"),
            pl.col("favorite_count").sum().alias("favorite_count"),
            pl.col("to_cart_count").sum().alias("to_cart_count"),
            pl.col("weighted_pair_count").sum().alias("weighted_pair_count"),
            pl.col("weighted_view_count").sum().alias("weighted_view_count"),
            pl.col("weighted_click_count").sum().alias("weighted_click_count"),
            pl.col("weighted_favorite_count").sum().alias("weighted_favorite_count"),
            pl.col("weighted_to_cart_count").sum().alias("weighted_to_cart_count"),
        )
        .select(schemas.DAILY_PAIR_WIDGET_COUNTS_COLUMNS)
        .sort(["pair_date", "item_id", "similar_item_id", "target_widget_name"])
    )


def _merge_daily_pair_keys(
        existing: pl.DataFrame,
        new: pl.DataFrame,
        columns: Sequence[str],
        sort_columns: Sequence[str],
) -> pl.DataFrame:
    merged = pl.concat([existing, new], how="vertical")
    if merged.is_empty():
        return empty_contract_frame(columns)

    return (
        merged.select(columns)
        .unique()
        .sort(sort_columns)
    )


def _user_bucket_filter(bucket_id: int, bucket_count: int) -> pl.Expr:
    """Return deterministic user bucket filter."""
    return (pl.col("user_id") % bucket_count) == bucket_id


def _iter_session_batches(
        sessions: pl.DataFrame,
        batch_size: int,
) -> list[pl.DataFrame]:
    """Split session rows into batches without splitting individual sessions."""
    if batch_size <= 0:
        raise ValueError("session batch_size must be a positive integer")

    if sessions.is_empty():
        return []

    session_keys = (
        sessions
        .select(["user_id", "session_index"])
        .unique()
        .sort(["user_id", "session_index"])
    )

    batches: list[pl.DataFrame] = []
    session_count = session_keys.height

    for start in range(0, session_count, batch_size):
        batch_keys = session_keys.slice(start, batch_size)
        batch = sessions.join(
            batch_keys,
            on=["user_id", "session_index"],
            how="inner",
        )
        batches.append(batch)

    return batches


def _write_daily_pair_stats(
        *,
        stats: DailyPairStats,
        partition_date: str,
        output_dir: Path,
        merge_existing: bool = False,
) -> tuple[Path, Path, Path, Path]:
    """Write compact daily pair-stat artifacts and return their paths."""
    counts_dir = output_dir / "counts"
    widget_counts_dir = output_dir / "widget_counts"
    user_keys_dir = output_dir / "user_keys"
    session_keys_dir = output_dir / "session_keys"

    counts_dir.mkdir(parents=True, exist_ok=True)
    widget_counts_dir.mkdir(parents=True, exist_ok=True)
    user_keys_dir.mkdir(parents=True, exist_ok=True)
    session_keys_dir.mkdir(parents=True, exist_ok=True)

    count_path = counts_dir / f"date={partition_date}.parquet"
    widget_count_path = widget_counts_dir / f"date={partition_date}.parquet"
    user_key_path = user_keys_dir / f"date={partition_date}.parquet"
    session_key_path = session_keys_dir / f"date={partition_date}.parquet"

    counts = stats.counts
    widget_counts = stats.widget_counts
    user_keys = stats.user_keys
    session_keys = stats.session_keys

    if merge_existing and count_path.exists():
        counts = _merge_daily_pair_counts(
            pl.read_parquet(count_path),
            counts,
        )
    if merge_existing and widget_count_path.exists():
        widget_counts = _merge_daily_pair_widget_counts(
            pl.read_parquet(widget_count_path),
            widget_counts,
        )
    if merge_existing and user_key_path.exists():
        user_keys = _merge_daily_pair_keys(
            pl.read_parquet(user_key_path),
            user_keys,
            schemas.DAILY_PAIR_USER_KEYS_COLUMNS,
            ["pair_date", "item_id", "similar_item_id", "user_id"],
        )
    if merge_existing and session_key_path.exists():
        session_keys = _merge_daily_pair_keys(
            pl.read_parquet(session_key_path),
            session_keys,
            schemas.DAILY_PAIR_SESSION_KEYS_COLUMNS,
            ["pair_date", "item_id", "similar_item_id", "user_id", "session_index"],
        )

    counts.write_parquet(count_path)
    widget_counts.write_parquet(widget_count_path)
    user_keys.write_parquet(user_key_path)
    session_keys.write_parquet(session_key_path)
    return count_path, widget_count_path, user_key_path, session_key_path


def _build_daily_pair_stats_in_memory(
        *,
        daily_sessions: Sequence[tuple[str, pl.DataFrame]],
        pair_builder: ItemPairBuilder,
        session_batch_size: int = 10_000,
) -> DailyPairStats:
    """Build compact daily pair stats from sessions without writing artifacts."""
    batch_stats: list[DailyPairStats] = []

    for partition_date, sessions in daily_sessions:
        session_batches = _iter_session_batches(
            sessions=sessions,
            batch_size=session_batch_size,
        )

        for batch_index, session_batch in enumerate(session_batches, start=1):

            stats = pair_builder.build_daily_pair_stats(session_batch)

            batch_stats.append(stats)

    return _combine_daily_pair_stats(batch_stats)


def _build_and_write_daily_pair_stats(
        *,
        daily_sessions: Sequence[tuple[str, pl.DataFrame]],
        pair_builder: ItemPairBuilder,
        output_dir: Path,
        session_batch_size: int = 10_000,
) -> DailyPairStatsPaths:
    """Build compact daily pair stats for each sessions partition and write once per day."""
    count_paths: list[Path] = []
    widget_count_paths: list[Path] = []
    user_key_paths: list[Path] = []
    session_key_paths: list[Path] = []
    raw_pair_rows = 0
    written_pair_dates: set[str] = set()

    output_dir.mkdir(parents=True, exist_ok=True)

    for partition_date, sessions in daily_sessions:
        batch_stats: list[DailyPairStats] = []
        session_batches = _iter_session_batches(
            sessions=sessions,
            batch_size=session_batch_size,
        )

        for batch_index, session_batch in enumerate(session_batches, start=1):

            stats = pair_builder.build_daily_pair_stats(session_batch)

            batch_stats.append(stats)
            raw_pair_rows += stats.raw_pair_rows

        if not batch_stats:
            continue

        combined_stats = _combine_daily_pair_stats(batch_stats)

        count_path, widget_count_path, user_key_path, session_key_path = _write_daily_pair_stats(
            stats=combined_stats,
            partition_date=partition_date,
            output_dir=output_dir,
            merge_existing=partition_date in written_pair_dates,
        )
        written_pair_dates.add(partition_date)

        count_paths.append(count_path)
        widget_count_paths.append(widget_count_path)
        user_key_paths.append(user_key_path)
        session_key_paths.append(session_key_path)

        del batch_stats
        del combined_stats

    return DailyPairStatsPaths(
        count_paths=count_paths,
        widget_count_paths=widget_count_paths,
        user_key_paths=user_key_paths,
        session_key_paths=session_key_paths,
        raw_pair_rows=raw_pair_rows,
    )


def _empty_session_index_state() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "user_id": pl.Series([], dtype=pl.Int64),
            "max_session_index": pl.Series([], dtype=pl.Int64),
        }
    )


def _empty_active_session_offsets() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "user_id": pl.Series([], dtype=pl.Int64),
            "active_session_index": pl.Series([], dtype=pl.Int64),
        }
    )


def _sessions_to_clean_events(sessions: pl.DataFrame) -> pl.DataFrame:
    """Convert active session rows back to clean-event shape for carry-over."""
    if sessions.is_empty():
        return empty_contract_frame(schemas.CLEAN_EVENTS_COLUMNS)

    return (
        sessions.select(
            "user_id",
            "event_date",
            "timestamp",
            "action_type",
            "item_id",
            "widget_name",
        )
        .with_columns(
            pl.lit(None, dtype=pl.Utf8).alias("search_query"),
            pl.col("widget_name").cast(pl.String).fill_null("unknown").alias("widget_name"),
        )
        .select(schemas.CLEAN_EVENTS_COLUMNS)
    )


def _session_index_offsets(
        max_session_indices: pl.DataFrame,
        active_session_indices: pl.DataFrame,
) -> pl.DataFrame:
    """Build per-user offsets for relative session indices in the next chunk."""
    offset_frames: list[pl.DataFrame] = []

    if not max_session_indices.is_empty():
        offset_frames.append(
            max_session_indices.select(
                "user_id",
                pl.col("max_session_index").alias("session_index_offset"),
            )
        )

    if not active_session_indices.is_empty():
        offset_frames.append(
            active_session_indices.select(
                "user_id",
                (pl.col("active_session_index") - 1).alias("session_index_offset"),
            )
        )

    if not offset_frames:
        return pl.DataFrame(
            {
                "user_id": pl.Series([], dtype=pl.Int64),
                "session_index_offset": pl.Series([], dtype=pl.Int64),
            }
        )

    return (
        pl.concat(offset_frames, how="vertical")
        .unique(subset=["user_id"], keep="last", maintain_order=True)
    )


def _apply_session_index_offsets(
        sessions: pl.DataFrame,
        offsets: pl.DataFrame,
) -> pl.DataFrame:
    if sessions.is_empty() or offsets.is_empty():
        return sessions.select(schemas.SESSIONS_COLUMNS)

    return (
        sessions.join(offsets, on="user_id", how="left")
        .with_columns(
            (
                    pl.col("session_index")
                    + pl.col("session_index_offset").fill_null(0)
            )
            .cast(pl.Int64)
            .alias("session_index")
        )
        .drop("session_index_offset")
        .select(schemas.SESSIONS_COLUMNS)
    )


def _update_max_session_indices(
        current: pl.DataFrame,
        sessions: pl.DataFrame,
) -> pl.DataFrame:
    if sessions.is_empty():
        return current

    updates = sessions.group_by("user_id").agg(
        pl.col("session_index").max().alias("max_session_index")
    )

    if current.is_empty():
        return updates.sort("user_id")

    return (
        pl.concat([current, updates], how="vertical")
        .group_by("user_id")
        .agg(pl.col("max_session_index").max().alias("max_session_index"))
        .sort("user_id")
    )


def _split_completed_and_active_sessions(
        sessions: pl.DataFrame,
        partition_date: str,
        timeout_minutes: int,
        is_final_partition: bool,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split sessions into flushable completed rows and active carry-over rows."""
    if sessions.is_empty():
        empty = empty_contract_frame(schemas.SESSIONS_COLUMNS)
        return empty, empty

    if is_final_partition:
        return sessions.select(schemas.SESSIONS_COLUMNS), empty_contract_frame(
            schemas.SESSIONS_COLUMNS
        )

    day_end = datetime.fromisoformat(partition_date) + timedelta(days=1)
    cutoff = day_end - timedelta(minutes=timeout_minutes)

    session_last_seen = sessions.group_by(["user_id", "session_index"]).agg(
        pl.col("timestamp").max().alias("__session_last_timestamp")
    )

    sessions_with_last_seen = sessions.join(
        session_last_seen,
        on=["user_id", "session_index"],
        how="left",
    )

    completed = (
        sessions_with_last_seen
        .filter(pl.col("__session_last_timestamp") <= cutoff)
        .drop("__session_last_timestamp")
        .select(schemas.SESSIONS_COLUMNS)
    )
    active = (
        sessions_with_last_seen
        .filter(pl.col("__session_last_timestamp") > cutoff)
        .drop("__session_last_timestamp")
        .select(schemas.SESSIONS_COLUMNS)
    )

    return completed, active


def _append_daily_session_partitions(
        sessions: pl.DataFrame,
        output_dir: Path,
) -> None:
    """Append completed sessions into event-date partitions."""
    if sessions.is_empty():
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    for partition_date, frame in _partition_frame_by_date_column(sessions, "event_date"):
        output_path = output_dir / f"date={partition_date}.parquet"
        frame_to_write = frame.select(schemas.SESSIONS_COLUMNS)

        frame_to_write.write_parquet(output_path)


def _write_session_state_artifacts(
        *,
        active_sessions: pl.DataFrame,
        max_session_indices: pl.DataFrame,
        completed_sessions_rows: int,
        partition_date: str,
        output_dir: Path,
        config: Mapping[str, Any],
        action_types: Sequence[str],
        raw_input_identity: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[Path, Path]:
    """Persist lightweight state needed to continue sessionization after a day."""
    active_dir = output_dir / "active_sessions"
    indices_dir = output_dir / "max_session_indices"
    active_dir.mkdir(parents=True, exist_ok=True)
    indices_dir.mkdir(parents=True, exist_ok=True)

    active_path = active_dir / f"date={partition_date}.parquet"
    indices_path = indices_dir / f"date={partition_date}.parquet"

    active_sessions.select(schemas.SESSIONS_COLUMNS).write_parquet(active_path)
    max_session_indices.select(["user_id", "max_session_index"]).write_parquet(indices_path)

    manifest = ArtifactManifest(
        artifact_type="session_state",
        date=partition_date,
        fingerprint=_session_state_fingerprint(
            config=config,
            action_types=action_types,
            partition_date=partition_date,
            raw_input_identity=raw_input_identity,
        ),
        paths={
            "active_sessions": _relative_to_root(active_path, output_dir),
            "max_session_indices": _relative_to_root(indices_path, output_dir),
        },
        rows={
            "active_sessions": active_sessions.height,
            "max_session_indices": max_session_indices.height,
            "completed_sessions_rows": completed_sessions_rows,
        },
    )
    write_manifest(_session_state_manifest_path(output_dir, partition_date), manifest)
    return active_path, indices_path


def _unique_paths(paths: Sequence[Path]) -> list[Path]:
    return list(dict.fromkeys(paths))


def _build_streaming_sessions_and_pair_stats(
        *,
        clean_event_paths: Sequence[Path],
        session_builder: SessionBuilder,
        pair_builder: ItemPairBuilder,
        daily_pairs_output_dir: Path,
        sessions_output_dir: Path | None,
        session_state_output_dir: Path | None = None,
        config: Mapping[str, Any] | None = None,
        action_types: Sequence[str] | None = None,
        raw_input_identity_by_date: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        session_batch_size: int = 10_000,
        session_user_buckets: int = 256,
        logger: logging.Logger | None = None,
) -> tuple[int, DailyPairStatsPaths]:
    """Stream clean-event partitions into sessions and compact pair stats.

    The helper carries only unfinished active session tails between daily
    partitions. Completed sessions are flushed to artifacts and immediately used
    to build compact daily pair stats.
    """
    if not clean_event_paths:
        return 0, DailyPairStatsPaths(
            count_paths=[],
            widget_count_paths=[],
            user_key_paths=[],
            session_key_paths=[],
            raw_pair_rows=0,
        )

    count_paths: list[Path] = []
    widget_count_paths: list[Path] = []
    user_key_paths: list[Path] = []
    session_key_paths: list[Path] = []
    raw_pair_rows = 0
    sessions_rows = 0
    written_pair_dates: set[str] = set()

    active_clean_events = empty_contract_frame(schemas.CLEAN_EVENTS_COLUMNS)
    active_sessions = empty_contract_frame(schemas.SESSIONS_COLUMNS)
    max_session_indices = _empty_session_index_state()
    progress_logger = logger or logging.getLogger(__name__)

    sorted_clean_event_paths = sorted(clean_event_paths)

    for index, clean_event_path in enumerate(sorted_clean_event_paths):
        partition_date = clean_event_path.stem.removeprefix("date=")
        is_final_partition = index == len(sorted_clean_event_paths) - 1

        clean_day = pl.read_parquet(clean_event_path).select(schemas.CLEAN_EVENTS_COLUMNS)
        progress_logger.info(
            "[run_pipeline] pair-stat partition date=%s clean_rows=%s buckets=%s final=%s",
            partition_date,
            clean_day.height,
            session_user_buckets,
            is_final_partition,
        )

        next_active_session_chunks: list[pl.DataFrame] = []
        completed_session_chunks_for_output: list[pl.DataFrame] = []
        daily_stats_by_partition: dict[str, list[DailyPairStats]] = {}

        for bucket_id in range(session_user_buckets):
            bucket_filter = _user_bucket_filter(bucket_id, session_user_buckets)

            clean_bucket = clean_day.filter(bucket_filter)
            active_clean_bucket = (
                active_clean_events.filter(bucket_filter)
                if not active_clean_events.is_empty()
                else active_clean_events
            )

            if clean_bucket.is_empty() and active_clean_bucket.is_empty():
                continue

            should_log_bucket = (
                bucket_id == 0
                or (bucket_id + 1) % 32 == 0
                or bucket_id + 1 == session_user_buckets
            )
            if should_log_bucket:
                progress_logger.info(
                    "[run_pipeline] pair-stat partition date=%s bucket=%s/%s clean_rows=%s active_rows=%s",
                    partition_date,
                    bucket_id + 1,
                    session_user_buckets,
                    clean_bucket.height,
                    active_clean_bucket.height,
                )

            if active_clean_bucket.is_empty():
                session_input = clean_bucket
            elif clean_bucket.is_empty():
                session_input = active_clean_bucket
            else:
                session_input = pl.concat(
                    [active_clean_bucket, clean_bucket],
                    how="vertical",
                ).select(schemas.CLEAN_EVENTS_COLUMNS)

            relative_sessions = session_builder.transform_day(session_input)

            active_sessions_bucket = (
                active_sessions.filter(bucket_filter)
                if not active_sessions.is_empty()
                else active_sessions
            )
            active_session_indices = (
                active_sessions_bucket.group_by("user_id").agg(
                    pl.col("session_index").max().alias("active_session_index")
                )
                if not active_sessions_bucket.is_empty()
                else _empty_active_session_offsets()
            )
            offsets = _session_index_offsets(
                max_session_indices=max_session_indices,
                active_session_indices=active_session_indices,
            )
            sessions = _apply_session_index_offsets(relative_sessions, offsets)

            completed_sessions, active_sessions_bucket = _split_completed_and_active_sessions(
                sessions=sessions,
                partition_date=partition_date,
                timeout_minutes=session_builder.timeout_minutes,
                is_final_partition=is_final_partition,
            )

            if not completed_sessions.is_empty():
                completed_session_chunks_for_output.append(completed_sessions)

                daily_sessions_for_pairs = _partition_sessions_by_session_start_date(
                    completed_sessions
                )

                for stats_partition_date, partition_sessions in daily_sessions_for_pairs:
                    daily_stats = _build_daily_pair_stats_in_memory(
                        daily_sessions=[(stats_partition_date, partition_sessions)],
                        pair_builder=pair_builder,
                        session_batch_size=session_batch_size,
                    )
                    progress_logger.info(
                        "[run_pipeline] pair-stat built date=%s source_partition=%s bucket=%s/%s sessions=%s raw_pairs=%s",
                        stats_partition_date,
                        partition_date,
                        bucket_id + 1,
                        session_user_buckets,
                        partition_sessions.height,
                        daily_stats.raw_pair_rows,
                    )

                    if daily_stats.raw_pair_rows > 0 or not daily_stats.counts.is_empty():
                        daily_stats_by_partition.setdefault(
                            stats_partition_date,
                            [],
                        ).append(daily_stats)
                        raw_pair_rows += daily_stats.raw_pair_rows

                sessions_rows += completed_sessions.height

            if not active_sessions_bucket.is_empty():
                next_active_session_chunks.append(active_sessions_bucket)

            max_session_indices = _update_max_session_indices(
                max_session_indices,
                sessions,
            )

        if completed_session_chunks_for_output and sessions_output_dir is not None:
            _append_daily_session_partitions(
                pl.concat(completed_session_chunks_for_output, how="vertical"),
                sessions_output_dir,
            )

        for stats_partition_date, stats_list in sorted(daily_stats_by_partition.items()):
            progress_logger.info(
                "[run_pipeline] write combined daily pair stats date=%s parts=%s raw_pairs=%s",
                stats_partition_date,
                len(stats_list),
                sum(stats.raw_pair_rows for stats in stats_list),
            )

            combined_stats = _combine_daily_pair_stats(stats_list)
            merge_existing = stats_partition_date in written_pair_dates
            manifest_path = _daily_pair_stats_manifest_path(
                daily_pairs_output_dir,
                stats_partition_date,
            )
            previous_manifest = (
                read_manifest(manifest_path)
                if merge_existing and config is not None and action_types is not None
                else None
            )
            count_path, widget_count_path, user_key_path, session_key_path = (
                _write_daily_pair_stats(
                    stats=combined_stats,
                    partition_date=stats_partition_date,
                    output_dir=daily_pairs_output_dir,
                    merge_existing=merge_existing,
                )
            )

            if config is not None and action_types is not None:
                raw_pair_rows_for_date = sum(stats.raw_pair_rows for stats in stats_list)
                processed_through_date = partition_date
                if previous_manifest is not None:
                    raw_pair_rows_for_date += int(
                        previous_manifest.rows.get("raw_pair_rows", 0)
                    )
                    previous_processed_through_date = previous_manifest.metadata.get(
                        "processed_through_date"
                    )
                    if isinstance(previous_processed_through_date, str):
                        processed_through_date = max(
                            previous_processed_through_date,
                            partition_date,
                        )

                manifest = ArtifactManifest(
                    artifact_type="daily_pair_stats",
                    date=stats_partition_date,
                    fingerprint=_daily_pair_stats_fingerprint(
                        config=config,
                        action_types=action_types,
                        partition_date=stats_partition_date,
                        processed_through_date=processed_through_date,
                        raw_input_identity=(raw_input_identity_by_date or {}).get(
                            stats_partition_date,
                            [],
                        ),
                    ),
                    paths={
                        "counts": _relative_to_root(
                            count_path,
                            daily_pairs_output_dir,
                        ),
                        "widget_counts": _relative_to_root(
                            widget_count_path,
                            daily_pairs_output_dir,
                        ),
                        "user_keys": _relative_to_root(
                            user_key_path,
                            daily_pairs_output_dir,
                        ),
                        "session_keys": _relative_to_root(
                            session_key_path,
                            daily_pairs_output_dir,
                        ),
                    },
                    rows={
                        "counts": int(pl.read_parquet(count_path).height),
                        "widget_counts": int(pl.read_parquet(widget_count_path).height),
                        "user_keys": int(pl.read_parquet(user_key_path).height),
                        "session_keys": int(pl.read_parquet(session_key_path).height),
                        "raw_pair_rows": raw_pair_rows_for_date,
                    },
                    metadata={"processed_through_date": processed_through_date},
                )
                write_manifest(manifest_path, manifest)

            written_pair_dates.add(stats_partition_date)
            count_paths.append(count_path)
            widget_count_paths.append(widget_count_path)
            user_key_paths.append(user_key_path)
            session_key_paths.append(session_key_path)

            del combined_stats

        active_sessions = (
            pl.concat(next_active_session_chunks, how="vertical")
            if next_active_session_chunks
            else empty_contract_frame(schemas.SESSIONS_COLUMNS)
        )
        active_clean_events = _sessions_to_clean_events(active_sessions)

        if session_state_output_dir is not None and config is not None and action_types is not None:
            completed_sessions_rows_for_day = sum(
                frame.height for frame in completed_session_chunks_for_output
            )
            _write_session_state_artifacts(
                active_sessions=active_sessions,
                max_session_indices=max_session_indices,
                completed_sessions_rows=completed_sessions_rows_for_day,
                partition_date=partition_date,
                output_dir=session_state_output_dir,
                config=config,
                action_types=action_types,
                raw_input_identity=(raw_input_identity_by_date or {}).get(
                    partition_date,
                    [],
                ),
            )

        progress_logger.info(
            "[run_pipeline] pair-stat partition done date=%s sessions_rows_total=%s raw_pairs_total=%s active_sessions=%s",
            partition_date,
            sessions_rows,
            raw_pair_rows,
            active_sessions.height,
        )

    return sessions_rows, DailyPairStatsPaths(
        count_paths=_unique_paths(count_paths),
        widget_count_paths=_unique_paths(widget_count_paths),
        user_key_paths=_unique_paths(user_key_paths),
        session_key_paths=_unique_paths(session_key_paths),
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


def _pair_aggregator_from_config(config: Mapping[str, Any]) -> Any:
    from_config = getattr(PairAggregator, "from_config", None)
    if callable(from_config):
        return from_config(config)
    return PairAggregator()


def _run_id(train_until_date: str, lookback_days: int) -> str:
    return f"run_{train_until_date}_lb{lookback_days}"


def _outputs_root(outputs_config: Mapping[str, Any]) -> Path:
    return _as_path(outputs_config.get("root_dir"), "outputs")


def publish_latest_run(run_result: PipelineRunResult, latest_dir: str | Path) -> Path:
    """Copy public recommendation outputs and manifest into the latest snapshot."""
    writer = RecommendationWriter()
    latest_path = Path(latest_dir)
    latest_recommendations_dir = latest_path / "recommendations"
    latest_recommendations_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(
        run_result.detailed_recommendations_path,
        latest_recommendations_dir / "detailed.parquet",
    )
    shutil.copy2(
        run_result.enriched_recommendations_path,
        latest_recommendations_dir / "enriched.parquet",
    )
    shutil.copy2(
        run_result.lookup_recommendations_path,
        latest_recommendations_dir / "lookup.parquet",
    )

    manifest = dict(run_result.manifest)
    manifest.update(
        {
            "detailed_recommendations_path": "recommendations/detailed.parquet",
            "enriched_recommendations_path": "recommendations/enriched.parquet",
            "widget_recommendations_path": "recommendations/lookup.parquet",
            "lookup_recommendations_path": "recommendations/lookup.parquet",
            "paths": {
                "detailed_recommendations_path": "recommendations/detailed.parquet",
                "enriched_recommendations_path": "recommendations/enriched.parquet",
                "widget_recommendations_path": "recommendations/lookup.parquet",
                "lookup_recommendations_path": "recommendations/lookup.parquet",
            },
        }
    )
    return writer.save_manifest(manifest, latest_path)


def run_pipeline(
        train_until_date: str,
        lookback_days: int,
        config_path: str | Path = "configs/baseline.yaml",
        *,
        output_dir: str | Path | None = None,
        run_id: str | None = None,
        update_latest: bool = True,
) -> PipelineRunResult:
    """Run recommendation pipeline over a rolling train window.

    Pipeline stages:
    1. load daily raw events;
    2. clean events by day;
    3. stream sessions over daily clean partitions;
    4. build compact daily item-pair stats;
    5. aggregate pairs over window;
    6. score pairs;
    7. select top-K;
    8. apply fallback layer (optional);
    9. save detailed recommendations;
    10. save widget output;
    11. optionally update latest snapshot.
    """
    logger = logging.getLogger(__name__)
    run_started = time.perf_counter()

    logger.info("[run_pipeline] load config=%s", config_path)
    config = load_yaml_config(config_path)
    pipeline_config = _as_mapping(config.get("pipeline", {}))
    artifacts_config = _as_mapping(config.get("artifacts", {}))
    outputs_config = _as_mapping(config.get("outputs", {}))
    topk_config = _as_mapping(config.get("topk", {}))
    update_strategy = _update_strategy(config)

    window_start, window_end = _window_bounds(train_until_date, lookback_days)
    partition_dates = _date_range_strings(window_start, window_end)
    action_types = _item_action_types(config)
    logger.info(
        "[run_pipeline] window=%s..%s lookback_days=%s action_types=%s update_strategy=%s",
        window_start,
        window_end,
        lookback_days,
        action_types,
        update_strategy,
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

    logger.info("[run_pipeline] load/reuse clean raw events by day")
    clean_events_result = _load_or_build_clean_events(
        config=config,
        data_config=data_config,
        cleaner=cleaner,
        action_types=action_types,
        window_start=window_start,
        window_end=window_end,
        output_dir=events_clean_output_dir,
        allow_empty_input=allow_empty_input,
        update_strategy=update_strategy,
        logger=logger,
    )
    clean_event_paths = clean_events_result.paths
    raw_events_rows = clean_events_result.raw_events_rows
    clean_events_rows = clean_events_result.clean_events_rows
    raw_input_identity_by_date = clean_events_result.raw_input_identity_by_date

    if raw_events_rows == 0:
        logger.warning(
            "[run_pipeline] raw events empty for window=%s..%s",
            window_start,
            window_end,
        )

    logger.info(
        "[run_pipeline] raw events loaded rows=%s clean events rows=%s days=%s output_dir=%s",
        raw_events_rows,
        clean_events_rows,
        len(clean_event_paths),
        events_clean_output_dir,
    )

    events_clean_input = _scan_parquet_paths_or_empty_frame(
        clean_event_paths,
        schemas.CLEAN_EVENTS_COLUMNS,
    )

    logger.info("[run_pipeline] stream sessions and compact daily item-pair stats")
    session_builder = SessionBuilder.from_config(config)
    pair_builder = ItemPairBuilder.from_config(config)
    logger.info("[run_pipeline] session builder config=%s", session_builder)

    daily_pairs_output_dir = _as_path(
        artifacts_config.get("daily_pairs_dir"),
        "data/processed/item_pairs",
    )
    session_state_output_dir = _as_path(
        artifacts_config.get("session_state_dir"),
        "data/processed/session_state",
    )

    sessions_dir = artifacts_config.get("sessions_dir")
    sessions_output_dir = (
        _as_path(sessions_dir, "data/processed/sessions")
        if isinstance(sessions_dir, str | Path)
        else None
    )

    session_batch_size = _as_positive_int(
        value=pipeline_config.get("session_batch_size"),
        default=10_000,
        parameter_name="pipeline.session_batch_size",
    )
    logger.info("[run_pipeline] session batch size=%s", session_batch_size)

    session_user_buckets = _as_positive_int(
        value=pipeline_config.get("session_user_buckets"),
        default=64,
        parameter_name="pipeline.session_user_buckets",
    )
    logger.info("[run_pipeline] session user buckets=%s", session_user_buckets)

    aggregation_item_buckets = _as_positive_int(
        value=pipeline_config.get("aggregation_item_buckets"),
        default=1,
        parameter_name="pipeline.aggregation_item_buckets",
    )
    logger.info("[run_pipeline] aggregation item buckets=%s", aggregation_item_buckets)

    pair_stats_plan = (
        _plan_reusable_daily_pair_stats(
            config=config,
            action_types=action_types,
            partition_dates=partition_dates,
            daily_pairs_output_dir=daily_pairs_output_dir,
            session_state_output_dir=session_state_output_dir,
            processed_through_date=window_end,
            raw_input_identity_by_date=raw_input_identity_by_date,
        )
        if update_strategy == "incremental" and not clean_events_result.rebuilt_days
        else None
    )
    can_reuse_pair_stats = (
        pair_stats_plan is not None
        and not pair_stats_plan.invalid_days
        and len(pair_stats_plan.reused_days) == len(partition_dates)
    )

    if can_reuse_pair_stats and pair_stats_plan is not None:
        logger.info(
            "[run_pipeline] reuse daily pair stats days=%s",
            pair_stats_plan.reused_days,
        )
        sessions_rows = pair_stats_plan.sessions_rows
        daily_pair_stats_paths = pair_stats_plan.paths
        rebuilt_pair_stat_days: list[str] = []
        reused_pair_stat_days = pair_stats_plan.reused_days
        earliest_affected_date: str | None = None
    else:
        invalid_days = pair_stats_plan.invalid_days if pair_stats_plan is not None else []
        logger.info(
            "[run_pipeline] rebuild daily pair stats from window_start invalid_days=%s clean_rebuilt_days=%s",
            invalid_days,
            clean_events_result.rebuilt_days,
        )
        sessions_rows, daily_pair_stats_paths = _build_streaming_sessions_and_pair_stats(
            clean_event_paths=clean_event_paths,
            session_builder=session_builder,
            pair_builder=pair_builder,
            daily_pairs_output_dir=daily_pairs_output_dir,
            sessions_output_dir=sessions_output_dir,
            session_state_output_dir=session_state_output_dir,
            config=config,
            action_types=action_types,
            raw_input_identity_by_date=raw_input_identity_by_date,
            session_batch_size=session_batch_size,
            session_user_buckets=session_user_buckets,
            logger=logger,
        )
        _finalize_daily_pair_stat_manifests(
            count_paths=daily_pair_stats_paths.count_paths,
            daily_pairs_output_dir=daily_pairs_output_dir,
            config=config,
            action_types=action_types,
            processed_through_date=window_end,
            raw_input_identity_by_date=raw_input_identity_by_date,
        )
        rebuilt_pair_stat_days = [
            path.stem.removeprefix("date=")
            for path in daily_pair_stats_paths.count_paths
        ]
        reused_pair_stat_days = []
        affected_candidates = [
            *clean_events_result.rebuilt_days,
            *invalid_days,
        ]
        earliest_affected_date = min(affected_candidates) if affected_candidates else window_start
    daily_pairs_rows = daily_pair_stats_paths.raw_pair_rows

    logger.info(
        "[run_pipeline] sessions rows=%s daily item pairs rows=%s days=%s output_dir=%s",
        sessions_rows,
        daily_pairs_rows,
        len(daily_pair_stats_paths.count_paths),
        daily_pairs_output_dir,
    )

    logger.info("[run_pipeline] build item popularity and action distribution")
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
        "[run_pipeline] item popularity rows=%s action_distribution rows=%s",
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

    logger.info("[run_pipeline] prepare scorer and top-k selector")
    scorer = CoVisitationScorer.from_config(config)
    if scorer.action_shares is None:
        derived_action_shares = _action_shares_from_distribution(action_distribution)
        if derived_action_shares is not None:
            scorer = replace(scorer, action_shares=derived_action_shares)

    del action_distribution

    top_k = _as_positive_int(
        value=topk_config.get("top_k", pipeline_config.get("top_k")),
        default=20,
        parameter_name="topk.top_k",
    )
    logger.info("[run_pipeline] select top_k=%s", top_k)
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
        deduplicate=False,
    )

    pair_aggregates_rows = 0
    pair_scores_rows = 0
    recommendation_parts: list[pl.DataFrame] = []

    pair_aggregates_dir = artifacts_config.get("pair_aggregates_dir")
    if aggregation_item_buckets > 1 and isinstance(pair_aggregates_dir, str | Path):
        logger.info(
            "[run_pipeline] skip pair_aggregates artifact in bucketed aggregation mode"
        )

    for bucket_id in range(aggregation_item_buckets):
        should_log_bucket = (
            bucket_id == 0
            or (bucket_id + 1) % 10 == 0
            or bucket_id + 1 == aggregation_item_buckets
        )

        if should_log_bucket:
            logger.info(
                "[run_pipeline] aggregate/score/top-k item bucket=%s/%s",
                bucket_id + 1,
                aggregation_item_buckets,
            )

        aggregator = _pair_aggregator_from_config(config)

        if aggregation_item_buckets == 1:
            pair_aggregates = aggregator.aggregate_window_from_daily_stats_paths(
                count_paths=daily_pair_stats_paths.count_paths,
                user_key_paths=daily_pair_stats_paths.user_key_paths,
                session_key_paths=daily_pair_stats_paths.session_key_paths,
                window_start=window_start,
                window_end=window_end,
            )
        else:
            pair_aggregates = aggregator.aggregate_window_from_daily_stats_paths(
                count_paths=daily_pair_stats_paths.count_paths,
                user_key_paths=daily_pair_stats_paths.user_key_paths,
                session_key_paths=daily_pair_stats_paths.session_key_paths,
                window_start=window_start,
                window_end=window_end,
                item_bucket_id=bucket_id,
                item_bucket_count=aggregation_item_buckets,
            )

        bucket_pair_aggregates_rows = pair_aggregates.height
        pair_aggregates_rows += bucket_pair_aggregates_rows
        if should_log_bucket:
            logger.info(
                "[run_pipeline] item bucket=%s/%s pair aggregates rows=%s",
                bucket_id + 1,
                aggregation_item_buckets,
                bucket_pair_aggregates_rows,
            )

        if aggregation_item_buckets == 1 and isinstance(pair_aggregates_dir, str | Path):
            _write_window_artifact(
                frame=pair_aggregates,
                output_dir=_as_path(pair_aggregates_dir, "data/processed/pair_aggregates"),
                window_start=window_start,
                window_end=window_end,
            )

        if scorer.normalize_by_item_popularity:
            pair_scores_lazy = scorer.score_lazy(
                pair_aggregates,
                item_popularity=item_popularity,
            )
        else:
            pair_scores_lazy = scorer.score_lazy(pair_aggregates)

        pair_scores = pair_scores_lazy.collect()
        pair_scores_rows += pair_scores.height
        del pair_scores_lazy
        del pair_aggregates

        bucket_recommendations = selector.select(pair_scores)

        score_detail_columns = [
            column
            for column in [
                "pair_count",
                "view_count",
                "click_count",
                "favorite_count",
                "to_cart_count",
                "weighted_pair_count",
                "weighted_view_count",
                "weighted_click_count",
                "weighted_favorite_count",
                "weighted_to_cart_count",
                "unique_users",
                "unique_sessions",
                "base_score",
            ]
            if column in pair_scores.columns and column not in bucket_recommendations.columns
        ]

        if score_detail_columns and not bucket_recommendations.is_empty():
            bucket_recommendations = bucket_recommendations.join(
                pair_scores.select(
                    ["item_id", "similar_item_id", *score_detail_columns]
                ),
                on=["item_id", "similar_item_id"],
                how="left",
            )

        del pair_scores

        if not bucket_recommendations.is_empty():
            recommendation_parts.append(bucket_recommendations)

        if should_log_bucket:
            logger.info(
                "[run_pipeline] item bucket=%s/%s recommendations rows=%s",
                bucket_id + 1,
                aggregation_item_buckets,
                bucket_recommendations.height,
            )

    recommendations = _concat_recommendation_parts(recommendation_parts)
    del recommendation_parts

    logger.info(
        "[run_pipeline] pair aggregates rows=%s",
        pair_aggregates_rows,
    )

    logger.info(
        "[run_pipeline] pair scores rows=%s calibration_used=%s",
        pair_scores_rows,
        scorer.action_shares is not None,
    )

    fallback_config = FallbackConfig.from_config(config, top_k=top_k)
    if fallback_config.enabled:
        logger.info(
            "[run_pipeline] apply fallback enabled=%s top_k=%s include_cold_start_items=%s",
            fallback_config.enabled,
            fallback_config.top_k,
            fallback_config.include_cold_start_items,
        )
        product_information = load_products(
            data_config,
            columns=schemas.PRODUCT_INFORMATION_COLUMNS,
        )
        recommendations = FallbackLayer(config=fallback_config).apply(
            recommendations,
            item_popularity=item_popularity,
            product_information=product_information,
        )
        del product_information

    del item_popularity

    recommendations_rows = recommendations.height
    fallback_rows = (
        recommendations
        .filter(pl.col("source").is_in(FALLBACK_SOURCE_LABELS))
        .height
    )
    if recommendations_rows == 0:
        logger.warning("[run_pipeline] recommendations empty")
    logger.info(
        "[run_pipeline] recommendations rows=%s fallback_rows=%s",
        recommendations_rows,
        fallback_rows,
    )

    outputs_root = _outputs_root(outputs_config)
    latest_dir = _as_path(outputs_config.get("latest_dir"), "outputs/latest")
    run_id = run_id or _run_id(train_until_date=train_until_date, lookback_days=lookback_days)
    run_dir = Path(output_dir).resolve() if output_dir is not None else outputs_root / "runs" / run_id
    writer = RecommendationWriter()

    logger.info("[run_pipeline] write outputs run_id=%s", run_id)
    recommendations_dir = run_dir / "recommendations"
    detailed_path = writer.save_detailed(recommendations, recommendations_dir / "detailed.parquet")
    logger.info("[run_pipeline] load product_information for enriched recommendations")
    products = load_products(data_config, columns=["item_id", "name"])
    enriched_path = writer.save_enriched(
        recommendations,
        products,
        recommendations_dir / "enriched.parquet",
    )
    widget_path = writer.save_widget_format(recommendations, recommendations_dir / "lookup.parquet")

    del recommendations
    del products

    detailed_relative_path = detailed_path.relative_to(run_dir).as_posix()
    enriched_relative_path = enriched_path.relative_to(run_dir).as_posix()
    widget_relative_path = widget_path.relative_to(run_dir).as_posix()
    manifest = {
        "run_id": run_id,
        "generated_at": datetime.now(UTC),
        "train_until_date": train_until_date,
        "lookback_days": lookback_days,
        "window_start": window_start,
        "window_end": window_end,
        "update_strategy": update_strategy,
        "score_method": scorer.method,
        "top_k": top_k,
        "calibration_used": scorer.action_shares is not None,
        "fallback_enabled": fallback_config.enabled,
        "fallback_source_label": fallback_config.source_label,
        "detailed_recommendations_path": detailed_relative_path,
        "enriched_recommendations_path": enriched_relative_path,
        "widget_recommendations_path": widget_relative_path,
        "lookup_recommendations_path": widget_relative_path,
        "paths": {
            "detailed_recommendations_path": detailed_relative_path,
            "enriched_recommendations_path": enriched_relative_path,
            "widget_recommendations_path": widget_relative_path,
            "lookup_recommendations_path": widget_relative_path,
        },
        "artifact_partitions": {
            "daily_pair_counts": len(daily_pair_stats_paths.count_paths),
            "daily_pair_widget_counts": len(daily_pair_stats_paths.widget_count_paths),
            "daily_pair_user_keys": len(daily_pair_stats_paths.user_key_paths),
            "daily_pair_session_keys": len(daily_pair_stats_paths.session_key_paths),
        },
        "rows": {
            "raw_events": raw_events_rows,
            "clean_events": clean_events_rows,
            "sessions": sessions_rows,
            "daily_pairs": daily_pairs_rows,
            "pair_aggregates": pair_aggregates_rows,
            "pair_scores": pair_scores_rows,
            "recommendations": recommendations_rows,
            "fallback_recommendations": fallback_rows,
        },
        "incremental": {
            "reused_clean_event_days": clean_events_result.reused_days,
            "rebuilt_clean_event_days": clean_events_result.rebuilt_days,
            "reused_pair_stat_days": reused_pair_stat_days,
            "rebuilt_pair_stat_days": rebuilt_pair_stat_days,
            "earliest_affected_date": earliest_affected_date,
        },
    }
    run_manifest_path = writer.save_manifest(manifest, run_dir)
    result = PipelineRunResult(
        run_id=run_id,
        run_dir=run_dir,
        manifest_path=run_manifest_path,
        detailed_recommendations_path=detailed_path,
        enriched_recommendations_path=enriched_path,
        lookup_recommendations_path=widget_path,
        manifest=manifest,
    )
    if update_latest and (recommendations_rows > 0 or allow_empty_latest_update):
        publish_latest_run(result, latest_dir)
    elif recommendations_rows == 0:
        logger.warning(
            "[run_pipeline] latest manifest not updated (empty recommendations, allow_empty_latest_update=%s)",
            allow_empty_latest_update,
        )

    elapsed_seconds = time.perf_counter() - run_started
    logger.info("[run_pipeline] finished in %.2fs", elapsed_seconds)
    return result
