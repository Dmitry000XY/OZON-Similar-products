"""Backward-compatible data loader imports for notebooks and old code.

The current data layer keeps config helpers in ``data.config`` and parquet
readers in ``data.readers``. Older notebooks imported those helpers from
``data.loaders``. This module intentionally re-exports the public helpers so
notebooks and type-checking do not break while the project converges on one
canonical API.
"""

from ozon_similar_products.data.config import get_path_from_config, load_configs
from ozon_similar_products.data.readers import (
    find_parquet_payload_dir,
    load_events,
    load_products,
    scan_events,
    scan_products,
)

__all__ = [
    "find_parquet_payload_dir",
    "get_path_from_config",
    "load_configs",
    "load_events",
    "load_products",
    "scan_events",
    "scan_products",
]
