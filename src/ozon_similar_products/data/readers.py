"""Readers for raw data sources."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import polars as pl

from ozon_similar_products.data.config import (
    ProjectConfig,
    get_path_from_config,
    load_configs,
)
from ozon_similar_products.data.partitions import collect_event_parquet_files
from ozon_similar_products.data.validation import validate_frame_has_columns


def _as_list(value: str | Iterable[str] | None) -> list[str] | None:
    """Convert a string or iterable of strings to a list."""
    if value is None:
        return None

    if isinstance(value, str):
        return [value]

    return list(value)


def find_parquet_payload_dir(
    base_dir: Path,
    payload_root_names: Iterable[str],
    parquet_glob: str,
) -> Path:
    """Find the directory that contains a parquet dataset.

    The function supports both layouts:

    - ``base_dir/date=.../*.parquet``
    - ``base_dir/<payload_root>/date=.../*.parquet``

    Args:
        base_dir: Base dataset directory from ``configs/paths.yaml``.
        payload_root_names: Optional root directory names from ``configs/data.yaml``.
        parquet_glob: Glob pattern used to find parquet files.

    Returns:
        Directory that contains parquet files.

    Raises:
        FileNotFoundError: If no matching parquet files are found.
    """
    candidates: list[Path] = []

    for root_name in payload_root_names:
        candidates.append(base_dir / root_name)

    candidates.append(base_dir)

    for candidate in candidates:
        if candidate.exists() and any(candidate.glob(parquet_glob)):
            return candidate

    checked = "\n".join(f"  - {candidate}" for candidate in candidates)
    msg = (
        "Could not find parquet payload directory.\n"
        f"Base directory: {base_dir}\n"
        f"Checked:\n{checked}"
    )
    raise FileNotFoundError(msg)


def scan_events(
    config: ProjectConfig | None = None,
    *,
    dates: Iterable[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    action_types: str | Iterable[str] | None = None,
    sample_days: int | None = None,
    columns: Iterable[str] | None = None,
    validate: bool = True,
) -> pl.LazyFrame:
    """Scan user events as a Polars LazyFrame.

    Args:
        config: Project config returned by ``load_configs``. When omitted,
            configs are loaded automatically.
        dates: Optional explicit list of dates to read.
        start_date: Optional inclusive date range start.
        end_date: Optional inclusive date range end.
        action_types: Optional action type or list of action types.
        sample_days: Optional number of first selected dates to read.
        columns: Optional subset of columns to select.
        validate: Whether to validate expected columns from ``configs/data.yaml``.

    Returns:
        LazyFrame with user events.

    Raises:
        FileNotFoundError: If event parquet files are not found.
        ValueError: If validation is enabled and expected columns are missing.
    """
    config = config or load_configs()

    user_actions_base_dir = get_path_from_config(
        config=config,
        section="data",
        key="user_actions_dir",
    )

    data_config = config["data"]["user_actions"]

    events_dir = find_parquet_payload_dir(
        base_dir=user_actions_base_dir,
        payload_root_names=data_config["payload_root_names"],
        parquet_glob=data_config["parquet_glob"],
    )

    parquet_files = collect_event_parquet_files(
        events_dir=events_dir,
        dates=dates,
        start_date=start_date,
        end_date=end_date,
        action_types=_as_list(action_types),
        sample_days=sample_days,
    )

    lazy_frame = pl.scan_parquet(
        [path.as_posix() for path in parquet_files],
        hive_partitioning=True,
    )

    if validate:
        validate_frame_has_columns(
            lazy_frame,
            data_config["expected_columns"],
            dataset_name="user_actions",
        )

    if columns is not None:
        lazy_frame = lazy_frame.select(list(columns))

    return lazy_frame


def _should_apply_sample_days(
    use_sample: bool,
    dates: Iterable[str] | None,
    start_date: str | None,
    end_date: str | None,
) -> bool:
    """Return whether the default day sampling should be applied.

    Explicit date filters have priority over the default sample mode. For
    example, ``load_events(start_date=..., end_date=...)`` must read the whole
    requested range, not only the first day of that range.
    """
    has_explicit_date_filter = (
        dates is not None or start_date is not None or end_date is not None
    )
    return use_sample and not has_explicit_date_filter


def load_events(
    config: ProjectConfig | None = None,
    *,
    use_sample: bool = True,
    sample_days: int = 1,
    sample_rows: int | None = None,
    dates: Iterable[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    action_types: str | Iterable[str] | None = None,
    columns: Iterable[str] | None = None,
    validate: bool = True,
) -> pl.DataFrame:
    """Load user events into memory.

    By default, the function reads only the first available day to avoid loading
    the full dataset accidentally. Explicit date filters disable the default
    ``sample_days`` behavior.

    Args:
        config: Project config returned by ``load_configs``. When omitted,
            configs are loaded automatically.
        use_sample: Whether to apply the default sample mode.
        sample_days: Number of first available days to read when sample mode is
            active and no explicit date filters are passed.
        sample_rows: Optional row limit applied after file selection.
        dates: Optional explicit list of dates to read.
        start_date: Optional inclusive date range start.
        end_date: Optional inclusive date range end.
        action_types: Optional action type or list of action types.
        columns: Optional subset of columns to select.
        validate: Whether to validate expected columns from ``configs/data.yaml``.

    Returns:
        DataFrame with user events.

    Raises:
        FileNotFoundError: If event parquet files are not found.
        ValueError: If validation is enabled and expected columns are missing.
    """
    effective_sample_days = (
        sample_days
        if _should_apply_sample_days(
            use_sample=use_sample,
            dates=dates,
            start_date=start_date,
            end_date=end_date,
        )
        else None
    )

    lazy_frame = scan_events(
        config=config,
        dates=dates,
        start_date=start_date,
        end_date=end_date,
        action_types=action_types,
        sample_days=effective_sample_days,
        columns=columns,
        validate=validate,
    )

    if sample_rows is not None:
        lazy_frame = lazy_frame.head(sample_rows)

    return lazy_frame.collect()


def _collect_product_parquet_files(
    products_dir: Path,
    parquet_glob: str,
) -> list[Path]:
    """Collect product parquet files.

    Args:
        products_dir: Product dataset directory.
        parquet_glob: Glob pattern used to find parquet files.

    Returns:
        Sorted list of product parquet files.

    Raises:
        FileNotFoundError: If no product parquet files are found.
    """
    parquet_files = sorted(products_dir.glob(parquet_glob))

    if not parquet_files:
        msg = f"No product parquet files found in: {products_dir}"
        raise FileNotFoundError(msg)

    return parquet_files


def scan_products(
    config: ProjectConfig | None = None,
    *,
    columns: Iterable[str] | None = None,
    validate: bool = True,
) -> pl.LazyFrame:
    """Scan product information as a Polars LazyFrame.

    Args:
        config: Project config returned by ``load_configs``. When omitted,
            configs are loaded automatically.
        columns: Optional subset of columns to select.
        validate: Whether to validate expected columns from ``configs/data.yaml``.

    Returns:
        LazyFrame with product information.

    Raises:
        FileNotFoundError: If product parquet files are not found.
        ValueError: If validation is enabled and expected columns are missing.
    """
    config = config or load_configs()

    product_base_dir = get_path_from_config(
        config=config,
        section="data",
        key="product_information_dir",
    )

    data_config = config["data"]["product_information"]

    products_dir = find_parquet_payload_dir(
        base_dir=product_base_dir,
        payload_root_names=data_config["payload_root_names"],
        parquet_glob=data_config["parquet_glob"],
    )

    parquet_files = _collect_product_parquet_files(
        products_dir=products_dir,
        parquet_glob=data_config["parquet_glob"],
    )

    lazy_frame = pl.scan_parquet([path.as_posix() for path in parquet_files])

    if validate:
        validate_frame_has_columns(
            lazy_frame,
            data_config["expected_columns"],
            dataset_name="product_information",
        )

    if columns is not None:
        lazy_frame = lazy_frame.select(list(columns))

    return lazy_frame


def load_products(
    config: ProjectConfig | None = None,
    *,
    columns: Iterable[str] | None = None,
    validate: bool = True,
) -> pl.DataFrame:
    """Load product information into memory.

    Args:
        config: Project config returned by ``load_configs``. When omitted,
            configs are loaded automatically.
        columns: Optional subset of columns to select.
        validate: Whether to validate expected columns from ``configs/data.yaml``.

    Returns:
        DataFrame with product information.

    Raises:
        FileNotFoundError: If product parquet files are not found.
        ValueError: If validation is enabled and expected columns are missing.
    """
    return scan_products(
        config=config,
        columns=columns,
        validate=validate,
    ).collect()
