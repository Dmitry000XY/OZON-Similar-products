"""Pure data helpers for the Streamlit recommendation demo."""

from __future__ import annotations

import json
import random
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import polars as pl

REQUIRED_RECOMMENDATION_COLUMNS = [
    "item_id",
    "item_name",
    "similar_item_id",
    "similar_item_name",
    "rank",
    "score",
    "source",
]

METRIC_KEYS = [
    "hit_rate_at_k",
    "recall_at_k",
    "ndcg_at_k",
    "mrr_at_k",
    "coverage_at_k",
    "to_cart_hit_rate_at_k",
    "to_cart_recall_at_k",
    "fallback_share_at_k",
    "popularity_bias_at_k",
]

_SOURCE_EXPLANATIONS = {
    "behavioral": "Users interacted with these items in similar sessions",
    "fallback_category_type_popular": "Fallback from same category/type",
    "fallback_category_popular": "Fallback from same category",
    "fallback_type_popular": "Fallback from same type",
    "fallback_brand_popular": "Fallback from same brand",
    "fallback_global_popular": "Popular fallback",
}

_SOURCE_GROUPS = {
    "behavioral": "brain behavioral",
    "fallback_category_type_popular": "puzzle category/type fallback",
    "fallback_category_popular": "puzzle category fallback",
    "fallback_type_popular": "puzzle type fallback",
    "fallback_brand_popular": "label brand fallback",
    "fallback_global_popular": "fire popular fallback",
}


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object from disk."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"JSON file must contain an object: {path}")
    return payload


def resolve_run_dir(manifest_path: Path) -> Path:
    """Best-effort run directory resolution from a run or latest manifest path."""

    manifest_path = manifest_path.expanduser().resolve()
    manifest = load_json(manifest_path)
    run_id = manifest.get("run_id")
    if isinstance(run_id, str) and run_id:
        candidate = manifest_path.parent.parent / "runs" / run_id
        if candidate.exists():
            return candidate.resolve()

    if manifest_path.parent.name == "latest":
        for key in ("enriched_recommendations_path", "detailed_recommendations_path"):
            value = _find_manifest_path(manifest, key)
            if value is None:
                continue
            candidate = _resolve_manifest_value(
                value,
                manifest_path=manifest_path,
                project_root=Path.cwd(),
            )
            if candidate is not None and candidate.parent.name == "recommendations":
                return candidate.parent.parent.resolve()

    return manifest_path.parent


def find_recommendation_path(
        *,
        manifest_path: Path | None,
        enriched_path: Path | None,
        detailed_path: Path | None,
) -> Path:
    """Resolve the recommendation parquet path using explicit paths or manifest metadata."""

    project_root = Path.cwd()
    if enriched_path is not None:
        return _resolve_existing_explicit_path(enriched_path, project_root=project_root)
    if detailed_path is not None:
        return _resolve_existing_explicit_path(detailed_path, project_root=project_root)
    if manifest_path is None:
        raise FileNotFoundError("Provide --enriched-path, --detailed-path, or --manifest-path")

    resolved_manifest_path = _resolve_existing_explicit_path(
        manifest_path,
        project_root=project_root,
    )
    manifest = load_json(resolved_manifest_path)

    for key in ("enriched_recommendations_path", "detailed_recommendations_path"):
        value = _find_manifest_path(manifest, key)
        if value is None:
            continue
        candidate = _resolve_manifest_value(
            value,
            manifest_path=resolved_manifest_path,
            project_root=project_root,
        )
        if candidate is not None:
            return candidate

    for relative_path in (
        Path("recommendations/enriched.parquet"),
        Path("recommendations/detailed.parquet"),
    ):
        candidate = (resolved_manifest_path.parent / relative_path).resolve()
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Could not find enriched.parquet or detailed.parquet from "
        f"manifest {resolved_manifest_path}"
    )


def load_recommendations(path: Path) -> pl.DataFrame:
    """Read recommendations parquet from disk."""

    return pl.read_parquet(path)


def normalize_recommendations(frame: pl.DataFrame) -> pl.DataFrame:
    """Normalize enriched or detailed recommendation rows for the demo."""

    normalized = frame.clone()
    for column in REQUIRED_RECOMMENDATION_COLUMNS:
        if column not in normalized.columns:
            normalized = normalized.with_columns(pl.lit(None).alias(column))

    return (
        normalized.select(REQUIRED_RECOMMENDATION_COLUMNS)
        .with_columns(
            pl.col("item_name").cast(pl.Utf8),
            pl.col("similar_item_name").cast(pl.Utf8),
            pl.col("source").cast(pl.Utf8),
            pl.col("rank").cast(pl.Int64),
            pl.col("score").cast(pl.Float64),
        )
        .sort(["item_id", "rank", "similar_item_id"])
    )


def build_item_catalog(frame: pl.DataFrame) -> pl.DataFrame:
    """Build a searchable catalog of items that have recommendations."""

    if frame.is_empty():
        return pl.DataFrame(
            schema={
                "item_id": pl.Int64,
                "item_name": pl.Utf8,
                "recommendation_count": pl.UInt32,
            }
        )

    return (
        frame.select("item_id", "item_name")
        .group_by("item_id")
        .agg(
            pl.col("item_name").drop_nulls().first().alias("item_name"),
            pl.len().alias("recommendation_count"),
        )
        .sort("item_id")
    )


def search_items(catalog: pl.DataFrame, query: str, limit: int = 30) -> pl.DataFrame:
    """Search catalog by exact item_id first, then case-insensitive name substring."""

    query = query.strip()
    if not query or catalog.is_empty():
        return catalog.head(0)

    if query.isdecimal():
        exact = catalog.filter(pl.col("item_id").cast(pl.Utf8) == query)
        if not exact.is_empty():
            return exact.head(limit)

    lowered_query = query.lower()
    return (
        catalog.filter(
            pl.col("item_name")
            .fill_null("")
            .str.to_lowercase()
            .str.contains(lowered_query, literal=True)
        )
        .sort("item_id")
        .head(limit)
    )


def choose_random_item(catalog: pl.DataFrame, seed: int | None = None) -> dict[str, Any] | None:
    """Choose a random item, preferring rows with a non-null product name."""

    if catalog.is_empty():
        return None

    preferred = catalog.filter(
        pl.col("item_name").is_not_null() & (pl.col("item_name").str.len_chars() > 0)
    )
    pool = preferred if not preferred.is_empty() else catalog
    rows = pool.to_dicts()
    return random.Random(seed).choice(rows) if rows else None


def recommendations_for_item(frame: pl.DataFrame, item_id: int | str, top_k: int) -> pl.DataFrame:
    """Return top-K recommendations for one item_id sorted by rank."""

    return (
        frame.filter(pl.col("item_id").cast(pl.Utf8) == str(item_id))
        .sort(["rank", "similar_item_id"])
        .head(top_k)
    )


def source_explanation(source: str | None) -> str:
    """Return a human-readable explanation for a recommendation source."""

    if source is None:
        return "Unknown source"
    return _SOURCE_EXPLANATIONS.get(source, "Unknown source")


def source_group(source: str | None) -> str:
    """Return a compact source label for tables and badges."""

    if source is None:
        return "question unknown source"
    return _SOURCE_GROUPS.get(source, "question unknown source")


def source_distribution(recommendations: pl.DataFrame) -> pl.DataFrame:
    """Count recommendations by source."""

    if recommendations.is_empty():
        return pl.DataFrame(schema={"source": pl.Utf8, "count": pl.UInt32})
    return (
        recommendations.with_columns(pl.col("source").fill_null("unknown").alias("source"))
        .group_by("source")
        .agg(pl.len().alias("count"))
        .sort("count", descending=True)
    )


def _find_manifest_path(manifest: Mapping[str, Any], key: str) -> str | None:
    value = manifest.get(key)
    if isinstance(value, str):
        return value

    paths = manifest.get("paths")
    if isinstance(paths, Mapping):
        nested_value = paths.get(key)
        if isinstance(nested_value, str):
            return nested_value
    return None


def _resolve_existing_explicit_path(path: Path, *, project_root: Path) -> Path:
    path = path.expanduser()
    candidates = [path] if path.is_absolute() else [project_root / path, path]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    raise FileNotFoundError(f"Path does not exist: {path}")


def _resolve_manifest_value(
        value: str,
        *,
        manifest_path: Path,
        project_root: Path,
) -> Path | None:
    raw_path = Path(value).expanduser()
    candidates = (
        [raw_path]
        if raw_path.is_absolute()
        else [
            manifest_path.parent / raw_path,
            project_root / raw_path,
        ]
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return None
