"""Tests for reusable EDA profiling helpers."""

import polars as pl

from ozon_similar_products.data.eda_profiling import (
    action_profile,
    null_profile,
    parquet_partition_profile,
    partition_row_counts,
    schema_overview,
)


def test_schema_and_null_profiles() -> None:
    """Schema and null profiles should expose compact dataframe diagnostics."""
    frame = pl.DataFrame(
        {
            "user_id": [1, 2, None],
            "action_type": ["view", "search", "click"],
            "item_id": [10, None, 20],
        }
    )

    schema = schema_overview(frame)
    nulls = null_profile(frame, columns=["user_id", "item_id"])

    assert schema["column"].to_list() == ["user_id", "action_type", "item_id"]
    assert nulls.filter(pl.col("column") == "item_id")["null_count"].item() == 1
    assert nulls.filter(pl.col("column") == "user_id")["row_count"].item() == 3


def test_action_profile_counts_missing_values_by_action() -> None:
    """Action profile should separate acceptable search nulls from item events."""
    frame = pl.DataFrame(
        {
            "user_id": [1, 1, 2, 3],
            "action_type": ["view", "search", "search", "to_cart"],
            "item_id": [10, None, None, 20],
            "search_query": [None, "milk", None, None],
        }
    )

    profile = action_profile(frame)
    search_row = profile.filter(pl.col("action_type") == "search").to_dicts()[0]
    view_row = profile.filter(pl.col("action_type") == "view").to_dicts()[0]

    assert search_row["rows"] == 2
    assert search_row["item_id_missing_rows"] == 2
    assert search_row["search_query_missing_rows"] == 1
    assert view_row["item_id_missing_rows"] == 0


def test_parquet_partition_profile_reads_hive_metadata(tmp_path) -> None:
    """Partition profiling should use parquet metadata and Hive path values."""
    first_partition = tmp_path / "date=2024-03-01" / "action_type=view"
    second_partition = tmp_path / "date=2024-03-01" / "action_type=click"
    first_partition.mkdir(parents=True)
    second_partition.mkdir(parents=True)

    pl.DataFrame({"user_id": [1, 2], "item_id": [10, 20]}).write_parquet(
        first_partition / "part-0.parquet"
    )
    pl.DataFrame({"user_id": [1], "item_id": [10]}).write_parquet(
        second_partition / "part-0.parquet"
    )

    profile = parquet_partition_profile(tmp_path)
    counts = partition_row_counts(tmp_path)

    assert profile["rows"].sum() == 3
    assert set(profile["action_type"].to_list()) == {"view", "click"}
    assert counts.filter(pl.col("action_type") == "view")["rows"].item() == 2
    assert counts.filter(pl.col("action_type") == "click")["files"].item() == 1
