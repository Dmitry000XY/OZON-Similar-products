"""Validation artifact cache helpers for offline evaluation."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from ozon_similar_products.config import PROJECT_ROOT, get_path_from_config, load_configs
from ozon_similar_products.evaluation.ground_truth import build_ground_truth_from_daily_pair_counts
from ozon_similar_products.evaluation.tracking import write_json


@dataclass(frozen=True)
class ValidationCacheResult:
    """Validation artifacts loaded from or written to the local cache."""

    validation_pair_counts: pl.DataFrame
    ground_truth: pl.DataFrame
    cache_dir: Path
    cache_key: str
    cache_hit: bool
    metadata: dict[str, Any]


def stable_json_hash(payload: Mapping[str, Any]) -> str:
    """Return a deterministic hash for JSON-serializable metadata."""
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _current_git_sha() -> str | None:
    """Return the current code revision for cache invalidation when available."""
    github_sha = os.environ.get("GITHUB_SHA")
    if github_sha:
        return github_sha.strip() or None

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    sha = result.stdout.strip()
    return sha or None


def _date_range_strings(start_date: date, end_date: date) -> list[str]:
    if start_date > end_date:
        raise ValueError("validation_start_date must be <= validation_end_date")
    return [
        (start_date + timedelta(days=offset)).isoformat()
        for offset in range((end_date - start_date).days + 1)
    ]


def _file_fingerprint(path: Path, root: Path) -> dict[str, Any]:
    """Return a cheap identity fingerprint without hashing large parquet contents."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"path": path.as_posix(), "exists": False}

    try:
        relative_path = path.relative_to(root).as_posix()
    except ValueError:
        relative_path = path.as_posix()

    return {
        "path": relative_path,
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _candidate_event_roots(user_actions_dir: Path, payload_root_names: Sequence[Any]) -> list[Path]:
    roots = [user_actions_dir / str(root_name) for root_name in payload_root_names]
    roots.append(user_actions_dir)
    return list(dict.fromkeys(roots))


def _validation_input_files(
    *,
    user_actions_dir: Path,
    payload_root_names: Sequence[Any],
    validation_dates: Sequence[str],
    item_action_types: Sequence[str],
) -> list[dict[str, Any]]:
    fingerprints: list[dict[str, Any]] = []
    action_types = [str(action_type) for action_type in item_action_types]

    for root in _candidate_event_roots(user_actions_dir, payload_root_names):
        for validation_date in validation_dates:
            date_dir = root / f"date={validation_date}"
            if not date_dir.exists():
                continue
            for action_type in action_types:
                action_dir = date_dir / f"action_type={action_type}"
                if action_dir.exists():
                    fingerprints.extend(
                        _file_fingerprint(path, user_actions_dir)
                        for path in sorted(action_dir.glob("*.parquet"))
                    )

    return sorted(fingerprints, key=lambda item: str(item["path"]))


def validation_data_identity(
    *,
    validation_start_date: date,
    validation_end_date: date,
    item_action_types: Sequence[str],
) -> dict[str, Any]:
    """Build a lightweight identity for validation raw inputs used by the cache."""
    project_config = load_configs(project_root=PROJECT_ROOT)
    user_actions_dir = get_path_from_config(project_config, "data", "user_actions_dir")
    product_information_dir = get_path_from_config(project_config, "data", "product_information_dir")
    user_actions_config = project_config["data"].get("user_actions", {})
    payload_root_names = (
        user_actions_config.get("payload_root_names", [])
        if isinstance(user_actions_config, Mapping)
        else []
    )
    validation_dates = _date_range_strings(validation_start_date, validation_end_date)
    input_files = _validation_input_files(
        user_actions_dir=user_actions_dir,
        payload_root_names=payload_root_names,
        validation_dates=validation_dates,
        item_action_types=item_action_types,
    )

    return {
        "project_root": Path(project_config["project_root"]).as_posix(),
        "user_actions_dir": user_actions_dir.as_posix(),
        "product_information_dir": product_information_dir.as_posix(),
        "validation_dates": validation_dates,
        "item_action_types": [str(action_type) for action_type in item_action_types],
        "paths_config_hash": stable_json_hash(project_config["paths"]),
        "data_config_hash": stable_json_hash(project_config["data"]),
        "input_files": input_files,
    }


def validation_cache_metadata(
    *,
    config: Mapping[str, Any],
    validation_start_date: date,
    validation_end_date: date,
    relevance_mode: str,
    relevance_weights: Mapping[str, Any] | None,
    item_action_types: list[str],
    git_sha: str | None,
) -> dict[str, Any]:
    """Build metadata for validation cache invalidation."""
    pipeline_config = config.get("pipeline", {})
    pair_builder_config = config.get("item_pair_builder", {})
    graph_config = config.get("graph", {})
    metadata_config = {
        "cache_schema_version": 3,
        "validation_start_date": validation_start_date.isoformat(),
        "validation_end_date": validation_end_date.isoformat(),
        "validation_data_identity": validation_data_identity(
            validation_start_date=validation_start_date,
            validation_end_date=validation_end_date,
            item_action_types=item_action_types,
        ),
        "item_action_types": item_action_types,
        "session_timeout_minutes": (
            pipeline_config.get("session_timeout_minutes")
            if isinstance(pipeline_config, Mapping)
            else None
        ),
        "max_items_per_session": (
            pipeline_config.get("max_items_per_session")
            if isinstance(pipeline_config, Mapping)
            else None
        ),
        "item_pair_builder": dict(pair_builder_config) if isinstance(pair_builder_config, Mapping) else {},
        "graph": dict(graph_config) if isinstance(graph_config, Mapping) else {},
        "relevance_mode": relevance_mode,
        "relevance_weights": dict(relevance_weights) if isinstance(relevance_weights, Mapping) else None,
    }
    return {
        **metadata_config,
        "git_sha": git_sha or _current_git_sha(),
        "config_hash": stable_json_hash(metadata_config),
    }


def validation_cache_key(metadata: Mapping[str, Any]) -> str:
    """Return a short stable cache key for validation metadata."""
    return stable_json_hash(metadata)[:24]


def read_validation_cache_metadata(path: Path) -> dict[str, Any] | None:
    """Read cache metadata from disk, returning None for absent or invalid JSON."""
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def load_or_build_validation_cache(
    *,
    cache_root: Path,
    metadata: Mapping[str, Any],
    relevance_mode: str,
    relevance_weights: Mapping[str, Any] | None,
    build_validation_pair_counts: Callable[[], pl.DataFrame],
    logger: logging.Logger,
) -> ValidationCacheResult:
    """Load validation artifacts from cache, or build and cache them."""
    cache_key = validation_cache_key(metadata)
    cache_dir = cache_root / cache_key
    pair_counts_path = cache_dir / "validation_pair_counts.parquet"
    ground_truth_path = cache_dir / "ground_truth.parquet"
    metadata_path = cache_dir / "metadata.json"

    cached_metadata = read_validation_cache_metadata(metadata_path)
    if (
        cached_metadata == dict(metadata)
        and pair_counts_path.exists()
        and ground_truth_path.exists()
    ):
        logger.info("[validation_cache] reuse key=%s dir=%s", cache_key, cache_dir)
        return ValidationCacheResult(
            validation_pair_counts=pl.read_parquet(pair_counts_path),
            ground_truth=pl.read_parquet(ground_truth_path),
            cache_dir=cache_dir,
            cache_key=cache_key,
            cache_hit=True,
            metadata=dict(metadata),
        )

    logger.info("[validation_cache] build key=%s dir=%s", cache_key, cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    validation_pair_counts = build_validation_pair_counts()
    ground_truth = build_ground_truth_from_daily_pair_counts(
        validation_pair_counts,
        relevance_mode=relevance_mode,
        action_weights=relevance_weights,
    )
    validation_pair_counts.write_parquet(pair_counts_path)
    ground_truth.write_parquet(ground_truth_path)
    write_json(metadata_path, dict(metadata))
    return ValidationCacheResult(
        validation_pair_counts=validation_pair_counts,
        ground_truth=ground_truth,
        cache_dir=cache_dir,
        cache_key=cache_key,
        cache_hit=False,
        metadata=dict(metadata),
    )
