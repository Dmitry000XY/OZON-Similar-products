"""Reusable profiling helpers for raw parquet EDA."""

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import polars as pl

FrameLike = pl.DataFrame | pl.LazyFrame


def _as_lazy(frame: FrameLike) -> pl.LazyFrame:
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


def _collect_schema(frame: FrameLike) -> pl.Schema:
    if isinstance(frame, pl.LazyFrame):
        return frame.collect_schema()
    return frame.schema


def schema_overview(frame: FrameLike) -> pl.DataFrame:
    """Return column names and dtypes for a dataframe or lazy scan."""
    schema = _collect_schema(frame)
    return pl.DataFrame(
        {
            "column": list(schema.keys()),
            "dtype": [str(dtype) for dtype in schema.values()],
        }
    )


def null_profile(frame: FrameLike, columns: Iterable[str] | None = None) -> pl.DataFrame:
    """Return null counts and shares for selected columns."""
    schema = _collect_schema(frame)
    selected_columns = list(columns) if columns is not None else list(schema.keys())
    missing_columns = set(selected_columns) - set(schema.keys())
    if missing_columns:
        raise ValueError(f"Missing columns for null profiling: {sorted(missing_columns)}")

    if not selected_columns:
        return pl.DataFrame(
            schema={
                "column": pl.Utf8,
                "row_count": pl.Int64,
                "null_count": pl.Int64,
                "null_share": pl.Float64,
            }
        )

    lazy_frame = _as_lazy(frame)
    row_count = lazy_frame.select(pl.len().alias("row_count")).collect().item()
    null_counts = lazy_frame.select(
        [pl.col(column).null_count().alias(column) for column in selected_columns]
    ).collect()
    null_count_by_column = null_counts.to_dicts()[0]

    return pl.DataFrame(
        {
            "column": selected_columns,
            "row_count": [row_count] * len(selected_columns),
            "null_count": [int(null_count_by_column[column]) for column in selected_columns],
            "null_share": [
                None if row_count == 0 else float(null_count_by_column[column]) / row_count
                for column in selected_columns
            ],
        }
    )


def action_profile(
    frame: FrameLike,
    action_col: str = "action_type",
    item_id_col: str = "item_id",
    search_query_col: str = "search_query",
    user_id_col: str = "user_id",
) -> pl.DataFrame:
    """Aggregate action counts and key missing-value shares by action type."""
    schema = _collect_schema(frame)
    if action_col not in schema:
        raise ValueError(f"Missing action column: {action_col}")

    aggregations: list[pl.Expr] = [pl.len().alias("rows")]
    if user_id_col in schema:
        aggregations.append(pl.col(user_id_col).drop_nulls().n_unique().alias("users"))
    if item_id_col in schema:
        aggregations.extend(
            [
                pl.col(item_id_col).drop_nulls().n_unique().alias("items"),
                pl.col(item_id_col).null_count().alias("item_id_missing_rows"),
            ]
        )
    if search_query_col in schema:
        aggregations.append(
            pl.col(search_query_col).null_count().alias("search_query_missing_rows")
        )

    result = (
        _as_lazy(frame)
        .group_by(action_col)
        .agg(aggregations)
        .collect()
        .sort(action_col)
    )
    total_rows = result["rows"].sum()
    result = result.with_columns((pl.col("rows") / total_rows).alias("share"))

    if "item_id_missing_rows" in result.columns:
        result = result.with_columns(
            (pl.col("item_id_missing_rows") / pl.col("rows")).alias("item_id_missing_share")
        )
    if "search_query_missing_rows" in result.columns:
        result = result.with_columns(
            (pl.col("search_query_missing_rows") / pl.col("rows")).alias(
                "search_query_missing_share"
            )
        )

    return result


def hive_partitions_from_path(path: str | Path) -> dict[str, str]:
    """Extract Hive-style partition values from a path."""
    partitions: dict[str, str] = {}
    for part in Path(path).parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key and value:
            partitions[key] = value
    return partitions


def parquet_files(path: str | Path, parquet_glob: str = "**/*.parquet") -> list[Path]:
    """Return parquet files below a file or directory path."""
    dataset_path = Path(path)
    if dataset_path.is_file():
        return [dataset_path]
    return sorted(file for file in dataset_path.glob(parquet_glob) if file.is_file())


def parquet_partition_profile(
    path: str | Path,
    parquet_glob: str = "**/*.parquet",
) -> pl.DataFrame:
    """Profile parquet files using metadata and Hive partition names."""
    dataset_path = Path(path)
    files = parquet_files(dataset_path, parquet_glob)
    records: list[dict[str, Any]] = []
    partition_columns: set[str] = set()

    for file in files:
        partitions = hive_partitions_from_path(file)
        partition_columns.update(partitions)
        file_path = str(file.relative_to(dataset_path)) if dataset_path.is_dir() else file.name
        records.append(
            {
                "file_path": file_path,
                "rows": parquet_num_rows(file),
                "file_size_bytes": int(file.stat().st_size),
                **partitions,
            }
        )

    if not records:
        return pl.DataFrame(
            schema={
                "file_path": pl.Utf8,
                "rows": pl.Int64,
                "file_size_bytes": pl.Int64,
            }
        )

    ordered_columns = ["file_path", "rows", "file_size_bytes", *sorted(partition_columns)]
    return pl.DataFrame(records).select(ordered_columns)


def parquet_num_rows(path: str | Path) -> int:
    """Return parquet row count, preferring metadata-only readers when available."""
    file = Path(path)
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return int(pl.scan_parquet(str(file)).select(pl.len()).collect().item())
    return int(pq.ParquetFile(file).metadata.num_rows)


def partition_row_counts(
    path: str | Path,
    partition_columns: Sequence[str] = ("date", "action_type"),
    parquet_glob: str = "**/*.parquet",
) -> pl.DataFrame:
    """Aggregate parquet metadata row counts by selected partition columns."""
    profile = parquet_partition_profile(path, parquet_glob)
    group_columns = [column for column in partition_columns if column in profile.columns]

    if not group_columns:
        return profile.select(
            pl.len().alias("files"),
            pl.col("rows").sum().alias("rows"),
            pl.col("file_size_bytes").sum().alias("file_size_bytes"),
        )

    return (
        profile.group_by(group_columns)
        .agg(
            pl.len().alias("files"),
            pl.col("rows").sum().alias("rows"),
            pl.col("file_size_bytes").sum().alias("file_size_bytes"),
        )
        .sort(group_columns)
    )


def parquet_dataset_overview(
    path: str | Path,
    parquet_glob: str = "**/*.parquet",
) -> pl.DataFrame:
    """Return one-row dataset size overview from parquet metadata."""
    profile = parquet_partition_profile(path, parquet_glob)
    return profile.select(
        pl.len().alias("files"),
        pl.col("rows").sum().alias("rows"),
        pl.col("file_size_bytes").sum().alias("file_size_bytes"),
    )
