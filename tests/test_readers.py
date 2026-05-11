"""Tests for data readers."""

from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml

import ozon_similar_products.data.config as data_config
from ozon_similar_products.data import load_events, load_products, scan_events, scan_products
from ozon_similar_products.data.config import (
    find_project_root,
    get_path_from_config,
    load_yaml,
    resolve_project_path,
)
from ozon_similar_products.data.readers import find_parquet_payload_dir
from ozon_similar_products.data.validation import validate_frame_has_columns


def make_test_config(project_root: Path) -> dict[str, Any]:
    """Create a minimal config compatible with readers.py."""
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
                    "name",
                    "brand",
                    "type",
                    "category_id",
                    "category_name",
                ],
            },
        },
    }


def write_product_dataset(project_root: Path) -> None:
    """Create a small product information parquet dataset."""
    products_dir = project_root / "data" / "raw" / "product_information"
    products_dir.mkdir(parents=True)

    pl.DataFrame(
        {
            "item_id": [1, 2, 3],
            "name": ["product_1", "product_2", "product_3"],
            "brand": ["brand_1", "brand_2", "brand_3"],
            "type": ["type_1", "type_2", "type_3"],
            "category_id": [10, 20, 30],
            "category_name": ["category_1", "category_2", "category_3"],
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


def write_event_dataset(project_root: Path) -> None:
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


@pytest.fixture
def test_config(tmp_path: Path) -> dict[str, Any]:
    """Return a minimal readers config rooted in a temporary directory."""
    return make_test_config(tmp_path)


@pytest.fixture
def product_config(tmp_path: Path) -> dict[str, Any]:
    """Return a config with a small product dataset prepared on disk."""
    write_product_dataset(tmp_path)
    return make_test_config(tmp_path)


@pytest.fixture
def event_config(tmp_path: Path) -> dict[str, Any]:
    """Return a config with a small event dataset prepared on disk."""
    write_event_dataset(tmp_path)
    return make_test_config(tmp_path)


def test_load_products_reads_product_parquet(
    product_config: dict[str, Any],
) -> None:
    """load_products should read product parquet files."""
    products = load_products(product_config)

    assert products.shape == (3, 6)
    assert products.columns == [
        "item_id",
        "name",
        "brand",
        "type",
        "category_id",
        "category_name",
    ]


def test_scan_products_returns_lazy_frame(product_config: dict[str, Any]) -> None:
    """scan_products should return a Polars LazyFrame."""
    products_lazy = scan_products(product_config)

    assert isinstance(products_lazy, pl.LazyFrame)
    assert products_lazy.collect().shape == (3, 6)


def test_load_events_reads_sample_and_hive_partitions(
    event_config: dict[str, Any],
) -> None:
    """load_events should read a row-limited sample from event partitions."""
    events = load_events(
        event_config,
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


def test_load_events_filters_by_action_type(event_config: dict[str, Any]) -> None:
    """load_events should support filtering by action_type."""
    events = load_events(
        event_config,
        use_sample=True,
        sample_days=1,
        action_types="click",
    )

    assert events.shape == (3, 7)
    assert set(events["action_type"].unique().to_list()) == {"click"}


def test_load_events_explicit_date_range_overrides_default_sample(
    event_config: dict[str, Any],
) -> None:
    """Explicit date ranges should not be truncated by default sample_days."""
    events = load_events(
        event_config,
        start_date="2024-03-01",
        end_date="2024-03-02",
    )

    assert events.shape == (6, 7)
    assert set(events["date"].cast(pl.String).unique().to_list()) == {
        "2024-03-01",
        "2024-03-02",
    }


def test_load_events_explicit_dates_override_default_sample(
    event_config: dict[str, Any],
) -> None:
    """Explicit date lists should not be truncated by default sample_days."""
    events = load_events(
        event_config,
        dates=["2024-03-01", "2024-03-02"],
    )

    assert events.shape == (6, 7)
    assert set(events["date"].cast(pl.String).unique().to_list()) == {
        "2024-03-01",
        "2024-03-02",
    }


def test_scan_events_returns_lazy_frame(event_config: dict[str, Any]) -> None:
    """scan_events should return a Polars LazyFrame."""
    events_lazy = scan_events(
        event_config,
        action_types=["click", "view"],
        sample_days=1,
    )

    assert isinstance(events_lazy, pl.LazyFrame)
    assert events_lazy.collect().shape == (4, 7)


def test_load_events_selects_columns(event_config: dict[str, Any]) -> None:
    """load_events should support reading a selected column subset."""
    events = load_events(
        event_config,
        use_sample=True,
        sample_days=1,
        columns=["user_id", "item_id", "action_type"],
    )

    assert events.shape == (4, 3)
    assert events.columns == ["user_id", "item_id", "action_type"]


def test_find_project_root_from_nested_directory(tmp_path: Path) -> None:
    """find_project_root should find configs/paths.yaml in parent directories."""
    project_root = tmp_path / "project"
    nested_dir = project_root / "notebooks" / "eda"

    (project_root / "configs").mkdir(parents=True)
    nested_dir.mkdir(parents=True)
    (project_root / "configs" / "paths.yaml").write_text(
        "data: {}\n",
        encoding="utf-8",
    )

    assert find_project_root(nested_dir) == project_root


def test_find_project_root_raises_when_config_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """find_project_root should fail when all search roots miss paths.yaml."""
    isolated_root = tmp_path / "isolated"
    fake_loader_file = isolated_root / "src" / "ozon_similar_products" / "data" / "readers.py"
    fake_loader_file.parent.mkdir(parents=True)
    fake_loader_file.write_text("", encoding="utf-8")

    monkeypatch.chdir(isolated_root)
    monkeypatch.setattr(data_config, "__file__", str(fake_loader_file))

    with pytest.raises(FileNotFoundError, match="Could not find project root"):
        find_project_root(isolated_root)


def test_load_yaml_reads_mapping(tmp_path: Path) -> None:
    """load_yaml should read YAML mappings."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("data:\n  raw_dir: data/raw\n", encoding="utf-8")

    assert load_yaml(config_path) == {"data": {"raw_dir": "data/raw"}}


def test_load_yaml_returns_empty_dict_for_empty_file(tmp_path: Path) -> None:
    """load_yaml should treat empty YAML files as empty dictionaries."""
    config_path = tmp_path / "empty.yaml"
    config_path.write_text("", encoding="utf-8")

    assert load_yaml(config_path) == {}


def test_load_yaml_raises_for_missing_file(tmp_path: Path) -> None:
    """load_yaml should fail when config file does not exist."""
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        load_yaml(tmp_path / "missing.yaml")


def test_load_yaml_raises_for_non_mapping_yaml(tmp_path: Path) -> None:
    """load_yaml should fail when YAML root is not a mapping."""
    config_path = tmp_path / "list.yaml"
    config_path.write_text(yaml.safe_dump(["a", "b"]), encoding="utf-8")

    with pytest.raises(TypeError, match="YAML config must contain a dictionary"):
        load_yaml(config_path)


def test_resolve_project_path_returns_absolute_path(tmp_path: Path) -> None:
    """resolve_project_path should resolve paths relative to project root."""
    config = {"project_root": tmp_path}

    assert resolve_project_path(config, "data/raw") == (tmp_path / "data/raw").resolve()


def test_get_path_from_config_reads_nested_path(tmp_path: Path) -> None:
    """get_path_from_config should read and resolve a configured path."""
    config = {
        "project_root": tmp_path,
        "paths": {
            "data": {
                "raw_dir": "data/raw",
            }
        },
    }

    assert get_path_from_config(config, "data", "raw_dir") == (tmp_path / "data/raw").resolve()


def test_validate_frame_has_columns_raises_for_missing_columns() -> None:
    """validate_frame_has_columns should fail when required columns are missing."""
    lazy_frame = pl.DataFrame({"item_id": [1, 2]}).lazy()

    with pytest.raises(ValueError, match="missing expected columns"):
        validate_frame_has_columns(
            lazy_frame,
            ["item_id", "sku"],
            dataset_name="product_information",
        )


def test_find_parquet_payload_dir_raises_when_no_parquet_exists(
    tmp_path: Path,
) -> None:
    """find_parquet_payload_dir should fail when parquet payload is missing."""
    base_dir = tmp_path / "data" / "raw" / "user_actions"
    base_dir.mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="Could not find parquet payload"):
        find_parquet_payload_dir(
            base_dir=base_dir,
            payload_root_names=["user_actions_3_months"],
            parquet_glob="**/*.parquet",
        )


def test_load_products_raises_when_dataset_is_missing(
    test_config: dict[str, Any],
) -> None:
    """load_products should fail clearly when product parquet files are absent."""
    with pytest.raises(FileNotFoundError, match="Could not find parquet payload"):
        load_products(test_config)


def test_load_events_raises_when_dataset_is_missing(
    test_config: dict[str, Any],
) -> None:
    """load_events should fail clearly when event parquet files are absent."""
    with pytest.raises(FileNotFoundError, match="Could not find parquet payload"):
        load_events(test_config)
