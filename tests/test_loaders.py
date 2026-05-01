"""Tests for data loaders."""

from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from ozon_similar_products.data import (
    load_events,
    load_products,
    scan_events,
    scan_products,
)


def make_test_config(project_root: Path) -> dict[str, Any]:
    """Create a minimal config compatible with loaders.py."""
    return {
        "project_root": project_root,
        "paths": {
            "data": {
                "user_actions_dir": "data/raw/user_actions",
                "product_information_dir": "data/raw/product_information",
            }
        },
        "data": {
            "user_actions": {
                "payload_root_names": [],
                "parquet_glob": "**/*.parquet",
                "expected_columns": [
                    "user_id",
                    "date",
                    "timestamp",
                    "action_type",
                    "widget_name",
                    "search_query",
                    "item_id",
                ],
            },
            "product_information": {
                "payload_root_names": [],
                "parquet_glob": "*.parquet",
                "expected_columns": [
                    "item_id",
                    "sku",
                    "name",
                ],
            },
        },
    }


def create_test_products(project_root: Path) -> None:
    """Create a small product information parquet dataset."""
    products_dir = project_root / "data" / "raw" / "product_information"
    products_dir.mkdir(parents=True)

    pl.DataFrame(
        {
            "item_id": [1, 2, 3],
            "sku": ["sku_1", "sku_2", "sku_3"],
            "name": ["product_1", "product_2", "product_3"],
        }
    ).write_parquet(products_dir / "products.parquet")


def write_event_partition(
    project_root: Path,
    date: str,
    action_type: str,
    frame: pl.DataFrame,
) -> None:
    """Write one Hive-style event partition."""
    partition_dir = (
        project_root
        / "data"
        / "raw"
        / "user_actions"
        / f"date={date}"
        / f"action_type={action_type}"
    )
    partition_dir.mkdir(parents=True)
    frame.write_parquet(partition_dir / "part-000.parquet")


def create_test_events(project_root: Path) -> None:
    """Create a small Hive-partitioned user actions parquet dataset."""
    write_event_partition(
        project_root=project_root,
        date="2024-03-01",
        action_type="click",
        frame=pl.DataFrame(
            {
                "user_id": [1, 2, 3],
                "timestamp": [
                    datetime(2024, 3, 1, 10, 0, 0),
                    datetime(2024, 3, 1, 10, 1, 0),
                    datetime(2024, 3, 1, 10, 2, 0),
                ],
                "widget_name": ["search", "search", "search"],
                "search_query": ["milk", "bread", "eggs"],
                "item_id": [101, 102, 103],
            }
        ),
    )

    write_event_partition(
        project_root=project_root,
        date="2024-03-01",
        action_type="view",
        frame=pl.DataFrame(
            {
                "user_id": [4],
                "timestamp": [datetime(2024, 3, 1, 11, 0, 0)],
                "widget_name": ["catalog"],
                "search_query": [None],
                "item_id": [104],
            }
        ),
    )

    write_event_partition(
        project_root=project_root,
        date="2024-03-02",
        action_type="click",
        frame=pl.DataFrame(
            {
                "user_id": [5, 6],
                "timestamp": [
                    datetime(2024, 3, 2, 12, 0, 0),
                    datetime(2024, 3, 2, 12, 1, 0),
                ],
                "widget_name": ["search", "search"],
                "search_query": ["cheese", "coffee"],
                "item_id": [105, 106],
            }
        ),
    )


def test_load_products_reads_product_parquet(tmp_path: Path) -> None:
    """load_products should read product parquet files."""
    create_test_products(tmp_path)
    config = make_test_config(tmp_path)

    products = load_products(config)

    assert products.shape == (3, 3)
    assert products.columns == ["item_id", "sku", "name"]


def test_scan_products_returns_lazy_frame(tmp_path: Path) -> None:
    """scan_products should return a Polars LazyFrame."""
    create_test_products(tmp_path)
    config = make_test_config(tmp_path)

    products_lazy = scan_products(config)

    assert isinstance(products_lazy, pl.LazyFrame)
    assert products_lazy.collect().shape == (3, 3)


def test_load_events_reads_sample_and_hive_partitions(tmp_path: Path) -> None:
    """load_events should read a row-limited sample from event partitions."""
    create_test_events(tmp_path)
    config = make_test_config(tmp_path)

    events = load_events(
        config,
        use_sample=True,
        sample_days=1,
        sample_rows=2,
    )

    assert events.shape == (2, 7)
    assert set(events.columns) == {
        "user_id",
        "date",
        "timestamp",
        "action_type",
        "widget_name",
        "search_query",
        "item_id",
    }


def test_load_events_filters_by_action_type(tmp_path: Path) -> None:
    """load_events should support filtering by action_type."""
    create_test_events(tmp_path)
    config = make_test_config(tmp_path)

    events = load_events(
        config,
        use_sample=True,
        sample_days=1,
        action_types="click",
    )

    assert events.shape == (3, 7)
    assert set(events["action_type"].unique().to_list()) == {"click"}


def test_load_events_explicit_date_range_overrides_default_sample(
    tmp_path: Path,
) -> None:
    """Explicit date ranges should not be truncated by default sample_days."""
    create_test_events(tmp_path)
    config = make_test_config(tmp_path)

    events = load_events(
        config,
        start_date="2024-03-01",
        end_date="2024-03-02",
    )

    assert events.shape == (6, 7)
    assert set(events["date"].cast(pl.String).unique().to_list()) == {
        "2024-03-01",
        "2024-03-02",
    }


def test_load_events_explicit_dates_override_default_sample(tmp_path: Path) -> None:
    """Explicit date lists should not be truncated by default sample_days."""
    create_test_events(tmp_path)
    config = make_test_config(tmp_path)

    events = load_events(
        config,
        dates=["2024-03-01", "2024-03-02"],
    )

    assert events.shape == (6, 7)
    assert set(events["date"].cast(pl.String).unique().to_list()) == {
        "2024-03-01",
        "2024-03-02",
    }


def test_scan_events_returns_lazy_frame(tmp_path: Path) -> None:
    """scan_events should return a Polars LazyFrame."""
    create_test_events(tmp_path)
    config = make_test_config(tmp_path)

    events_lazy = scan_events(
        config,
        action_types=["click", "view"],
        sample_days=1,
    )

    assert isinstance(events_lazy, pl.LazyFrame)
    assert events_lazy.collect().shape == (4, 7)


def test_load_events_selects_columns(tmp_path: Path) -> None:
    """load_events should support reading a selected column subset."""
    create_test_events(tmp_path)
    config = make_test_config(tmp_path)

    events = load_events(
        config,
        use_sample=True,
        sample_days=1,
        columns=["user_id", "item_id", "action_type"],
    )

    assert events.shape == (4, 3)
    assert events.columns == ["user_id", "item_id", "action_type"]
