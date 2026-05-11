"""Recommendation output writers."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from ozon_similar_products.data import schemas
from ozon_similar_products.data.validation import (
    validate_recommendations,
    validate_widget_output,
)

FrameLike = pl.DataFrame | pl.LazyFrame

DEFAULT_DETAILED_FILENAME = "recommendations.parquet"
DEFAULT_WIDGET_FILENAME = "similar_items.parquet"
DEFAULT_MANIFEST_FILENAME = "manifest.json"

_MANIFEST_PATH_KEYS = {
    "detailed_recommendations_path",
    "widget_recommendations_path",
    "compact_recommendations_path",
    "similar_items_path",
    "widget_path",
    "recommendations_path",
}


class RecommendationWriter:
    """Save detailed, compact and manifest recommendation outputs.

    The writer is responsible only for materializing already prepared
    recommendations and metadata. It does not calculate scores, does not select
    top-K, and does not perform lookup.
    """

    def save_detailed(
        self,
        recommendations: FrameLike,
        output_path: str | Path,
    ) -> Path:
        """Save item_id, similar_item_id, score, rank, source recommendations.

        Args:
            recommendations: Recommendations DataFrame or LazyFrame following
                the recommendations contract.
            output_path: Output parquet path or a directory. If a directory path
                is passed, ``recommendations.parquet`` is used as the file name.

        Returns:
            Path to the written parquet file.
        """
        validate_recommendations(recommendations)
        resolved_path = _resolve_output_path(
            output_path=output_path,
            default_filename=DEFAULT_DETAILED_FILENAME,
        )

        frame = _collect_if_lazy(recommendations)
        frame.write_parquet(resolved_path)
        return resolved_path

    def save_widget_format(
        self,
        recommendations: FrameLike,
        output_path: str | Path,
    ) -> Path:
        """Save compact item_id -> similar items output for lookup.

        Args:
            recommendations: Detailed recommendations DataFrame or LazyFrame.
                The input must follow the recommendations contract and contain
                ranked item-to-item rows.
            output_path: Output parquet path or a directory. If a directory path
                is passed, ``similar_items.parquet`` is used as the file name.

        Returns:
            Path to the written parquet file.
        """
        widget_output = self.to_widget_format(recommendations)
        resolved_path = _resolve_output_path(
            output_path=output_path,
            default_filename=DEFAULT_WIDGET_FILENAME,
        )

        widget_output.write_parquet(resolved_path)
        return resolved_path

    def to_widget_format(self, recommendations: FrameLike) -> pl.DataFrame:
        """Convert detailed recommendations to compact lookup format.

        The compact output contains one row per item_id and a rank-ordered list
        of similar items. The list column name follows the current project
        contract: ``similar_items_sku_list``.
        """
        validate_recommendations(recommendations)

        widget_output = (
            _as_lazy(recommendations)
            .filter(pl.col("item_id").is_not_null())
            .filter(pl.col("similar_item_id").is_not_null())
            .filter(pl.col("rank").is_not_null())
            .sort(["item_id", "rank", "similar_item_id"])
            .group_by("item_id", maintain_order=True)
            .agg(pl.col("similar_item_id").alias(schemas.WIDGET_OUTPUT_COLUMNS[1]))
            .select(schemas.WIDGET_OUTPUT_COLUMNS)
            .collect()
        )

        validate_widget_output(widget_output)
        return widget_output

    def save_manifest(
        self,
        manifest: Mapping[str, Any],
        output_path: str | Path,
    ) -> Path:
        """Save a JSON manifest that describes one recommendation run.

        Args:
            manifest: JSON-serializable run metadata. ``Path`` and datetime-like
                values are converted to strings automatically.
            output_path: Manifest JSON path or a directory. If a directory path
                is passed, ``manifest.json`` is used as the file name.

        Returns:
            Path to the written manifest file.
        """
        if not isinstance(manifest, Mapping):
            raise TypeError("manifest must be a mapping")

        resolved_path = _resolve_output_path(
            output_path=output_path,
            default_filename=DEFAULT_MANIFEST_FILENAME,
        )
        json_ready_manifest = _json_ready(dict(manifest))

        resolved_path.write_text(
            json.dumps(json_ready_manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return resolved_path

    def update_latest_manifest(
        self,
        run_manifest_path: str | Path,
        latest_dir: str | Path,
    ) -> Path:
        """Publish a run manifest as the latest snapshot manifest.

        Relative recommendation paths inside the run manifest are rebased so that
        they remain valid from ``latest/manifest.json``. This allows
        ``SimilarItemsLookup`` to read the latest manifest directly.

        Args:
            run_manifest_path: Existing manifest for a specific run.
            latest_dir: Directory where the latest manifest should be written.

        Returns:
            Path to ``latest/manifest.json``.
        """
        source_manifest_path = Path(run_manifest_path)
        manifest = _load_manifest(source_manifest_path)
        latest_path = _resolve_output_path(
            output_path=latest_dir,
            default_filename=DEFAULT_MANIFEST_FILENAME,
        )

        rebased_manifest = _rebase_manifest_paths(
            manifest=manifest,
            source_base=source_manifest_path.parent,
            target_base=latest_path.parent,
        )
        return self.save_manifest(rebased_manifest, latest_path)


def _collect_if_lazy(frame: FrameLike) -> pl.DataFrame:
    """Return an eager DataFrame for both DataFrame and LazyFrame inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame.collect()
    return frame


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    """Return a LazyFrame for both eager and lazy Polars inputs."""
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


def _resolve_output_path(
    output_path: str | Path,
    default_filename: str,
) -> Path:
    """Resolve an output path and create its parent directory.

    If the passed path has a suffix, it is treated as a file path. Otherwise it
    is treated as a directory path and ``default_filename`` is appended.
    """
    path = Path(output_path)

    if path.suffix:
        resolved_path = path
    else:
        resolved_path = path / default_filename

    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    return resolved_path


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    """Load a manifest JSON file as a dictionary."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise TypeError("manifest JSON must contain an object")
    return manifest


def _json_ready(value: Any) -> Any:
    """Convert common Python objects to JSON-compatible values."""
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_ready(item) for item in value]
    return value


def _rebase_manifest_paths(
    manifest: dict[str, Any],
    source_base: Path,
    target_base: Path,
) -> dict[str, Any]:
    """Rebase known path values from one manifest location to another."""
    return {
        key: _rebase_value(value, key=key, source_base=source_base, target_base=target_base)
        for key, value in manifest.items()
    }


def _rebase_value(
    value: Any,
    key: str,
    source_base: Path,
    target_base: Path,
) -> Any:
    """Recursively rebase known manifest path fields."""
    if isinstance(value, Mapping):
        return {
            str(child_key): _rebase_value(
                child_value,
                key=str(child_key),
                source_base=source_base,
                target_base=target_base,
            )
            for child_key, child_value in value.items()
        }

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [
            _rebase_value(
                item,
                key=key,
                source_base=source_base,
                target_base=target_base,
            )
            for item in value
        ]

    if isinstance(value, str) and key in _MANIFEST_PATH_KEYS:
        return _rebase_path_string(value, source_base=source_base, target_base=target_base)

    return _json_ready(value)


def _rebase_path_string(path_value: str, source_base: Path, target_base: Path) -> str:
    """Return a path string that is valid relative to the target directory."""
    source_path = Path(path_value)
    absolute_path = source_path if source_path.is_absolute() else source_base / source_path
    relative_path = os.path.relpath(absolute_path.resolve(), start=target_base.resolve())
    return Path(relative_path).as_posix()
