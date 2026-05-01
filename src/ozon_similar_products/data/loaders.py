from pathlib import Path
from typing import Any, Iterable

import polars as pl
import yaml

ProjectConfig = dict[str, Any]


def find_project_root(start: Path | None = None) -> Path:
    """Find the project root by looking for ``configs/paths.yaml``.

    Args:
        start: Optional path to start the search from. When omitted, the
            current working directory and this file location are used.

    Returns:
        Path to the project root.

    Raises:
        FileNotFoundError: If no parent directory contains ``configs/paths.yaml``.
    """
    start_points: list[Path] = []

    if start is not None:
        start_points.append(Path(start).resolve())

    start_points.append(Path.cwd().resolve())
    start_points.append(Path(__file__).resolve())

    for start_point in start_points:
        for candidate in [start_point, *start_point.parents]:
            if (candidate / "configs" / "paths.yaml").exists():
                return candidate

    msg = "Could not find project root. Expected configs/paths.yaml somewhere above."
    raise FileNotFoundError(msg)


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file as a dictionary.

    Args:
        path: Path to a YAML file.

    Returns:
        Parsed YAML content. Empty YAML files are returned as an empty dict.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        TypeError: If the YAML root object is not a dictionary.
    """
    path = Path(path)

    if not path.exists():
        msg = f"Config file not found: {path}"
        raise FileNotFoundError(msg)

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    if data is None:
        return {}

    if not isinstance(data, dict):
        msg = f"YAML config must contain a dictionary: {path}"
        raise TypeError(msg)

    return data


def load_configs(
        config_dir: str | Path = "configs",
        project_root: str | Path | None = None,
) -> ProjectConfig:
    """Load project path and data configs.

    Args:
        config_dir: Directory with project configs, relative to the project root.
        project_root: Optional explicit project root. When omitted, the root is
            detected automatically.

    Returns:
        Dictionary with ``project_root``, ``paths`` and ``data`` keys.
    """
    root = find_project_root(Path(project_root)) if project_root else find_project_root()
    config_path = root / config_dir

    return {
        "project_root": root,
        "paths": load_yaml(config_path / "paths.yaml"),
        "data": load_yaml(config_path / "data.yaml"),
    }


def resolve_project_path(config: ProjectConfig, relative_path: str | Path) -> Path:
    """Resolve a project-relative path.

    Args:
        config: Project config returned by ``load_configs``.
        relative_path: Path relative to the project root.

    Returns:
        Absolute resolved path.
    """
    root = Path(config["project_root"])
    return (root / relative_path).resolve()


def get_path_from_config(config: ProjectConfig, section: str, key: str) -> Path:
    """Read a path from ``configs/paths.yaml`` and resolve it.

    Args:
        config: Project config returned by ``load_configs``.
        section: Top-level section inside ``paths.yaml``.
        key: Path key inside the selected section.

    Returns:
        Absolute resolved path.
    """
    relative_path = config["paths"][section][key]
    return resolve_project_path(config, relative_path)


def _as_list(value: str | Iterable[str] | None) -> list[str] | None:
    """Convert a string or iterable of strings to a list."""
    if value is None:
        return None

    if isinstance(value, str):
        return [value]

    return list(value)


def _schema_names(lazy_frame: pl.LazyFrame) -> set[str]:
    """Return column names from a Polars LazyFrame schema."""
    try:
        return set(lazy_frame.collect_schema().names())
    except AttributeError:
        return set(lazy_frame.schema.keys())


def validate_columns(
        lazy_frame: pl.LazyFrame,
        expected_columns: Iterable[str],
        dataset_name: str,
) -> None:
    """Validate that a LazyFrame contains all expected columns.

    Args:
        lazy_frame: LazyFrame to validate.
        expected_columns: Required column names.
        dataset_name: Dataset name used in the error message.

    Raises:
        ValueError: If at least one expected column is missing.
    """
    actual_columns = _schema_names(lazy_frame)
    expected_columns = set(expected_columns)
    missing_columns = expected_columns - actual_columns

    if missing_columns:
        msg = (
            f"{dataset_name}: missing expected columns: {sorted(missing_columns)}. "
            f"Actual columns: {sorted(actual_columns)}"
        )
        raise ValueError(msg)


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


def list_event_dates(events_dir: Path) -> list[str]:
    """List available event dates from a Hive-partitioned dataset.

    Args:
        events_dir: Directory with ``date=YYYY-MM-DD`` partitions.

    Returns:
        Sorted list of available date strings.
    """
    dates: list[str] = []

    for path in events_dir.glob("date=*"):
        if path.is_dir() and "=" in path.name:
            _, value = path.name.split("=", maxsplit=1)
            dates.append(value)

    return sorted(dates)


def _filter_dates(
        available_dates: list[str],
        dates: Iterable[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        sample_days: int | None = None,
) -> list[str]:
    """Filter available dates by explicit list, range and optional sample size."""
    if dates is not None:
        selected = sorted(set(dates))
    else:
        selected = available_dates

    if start_date is not None:
        selected = [date for date in selected if date >= start_date]

    if end_date is not None:
        selected = [date for date in selected if date <= end_date]

    if sample_days is not None:
        selected = selected[:sample_days]

    return selected


def _collect_event_parquet_files(
        events_dir: Path,
        dates: Iterable[str] | None = None,
        action_types: Iterable[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        sample_days: int | None = None,
) -> list[Path]:
    """Collect parquet files for selected dates and action types.

    Args:
        events_dir: Event dataset directory with ``date=`` partitions.
        dates: Optional explicit list of dates.
        action_types: Optional list of action types.
        start_date: Optional inclusive date range start.
        end_date: Optional inclusive date range end.
        sample_days: Optional number of first selected dates to keep.

    Returns:
        Sorted list of parquet file paths.

    Raises:
        FileNotFoundError: If no parquet files match the filters.
    """
    available_dates = list_event_dates(events_dir)

    selected_dates = _filter_dates(
        available_dates=available_dates,
        dates=dates,
        start_date=start_date,
        end_date=end_date,
        sample_days=sample_days,
    )

    selected_action_types = _as_list(action_types)
    parquet_files: list[Path] = []

    for date in selected_dates:
        date_dir = events_dir / f"date={date}"

        if selected_action_types is None:
            action_dirs = sorted(date_dir.glob("action_type=*"))
        else:
            action_dirs = [
                date_dir / f"action_type={action_type}"
                for action_type in selected_action_types
            ]

        for action_dir in action_dirs:
            if action_dir.exists():
                parquet_files.extend(sorted(action_dir.glob("*.parquet")))

    if not parquet_files:
        msg = (
            "No event parquet files found for selected filters. "
            f"events_dir={events_dir}, "
            f"dates={dates}, "
            f"start_date={start_date}, "
            f"end_date={end_date}, "
            f"sample_days={sample_days}, "
            f"action_types={selected_action_types}"
        )
        raise FileNotFoundError(msg)

    return parquet_files


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

    parquet_files = _collect_event_parquet_files(
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
        validate_columns(
            lazy_frame=lazy_frame,
            expected_columns=data_config["expected_columns"],
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
        validate_columns(
            lazy_frame=lazy_frame,
            expected_columns=data_config["expected_columns"],
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
