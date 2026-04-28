"""Small parquet loading helpers for raw Ozon data."""

from pathlib import Path

import polars as pl

from ozon_similar_products.config import load_data_config
from ozon_similar_products.paths import PRODUCT_INFORMATION_DIR, USER_ACTIONS_DIR
from ozon_similar_products.data.validation import validate_columns


def load_parquet_dataset(path: str | Path, parquet_glob: str = "**/*.parquet") -> pl.DataFrame:
    """Load a parquet file or a directory with parquet files."""
    dataset_path = Path(path)
    if dataset_path.is_dir():
        return pl.scan_parquet(str(dataset_path / parquet_glob)).collect()
    return pl.read_parquet(dataset_path)


def load_product_information(path: str | Path | None = None) -> pl.DataFrame:
    """Load product information parquet data from a file or directory."""
    data_config = load_data_config()["product_information"]
    frame = load_parquet_dataset(path or PRODUCT_INFORMATION_DIR, data_config["parquet_glob"])
    validate_columns(frame.columns, data_config["expected_columns"])
    return frame


def load_user_actions(path: str | Path | None = None) -> pl.DataFrame:
    """Load user action parquet data from a file or partitioned directory."""
    data_config = load_data_config()["user_actions"]
    frame = load_parquet_dataset(path or USER_ACTIONS_DIR, data_config["parquet_glob"])
    validate_columns(frame.columns, data_config["expected_columns"])
    return frame
