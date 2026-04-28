from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import polars as pl
import yaml


ProjectConfig = dict[str, Any]


def find_project_root(start: Path | None = None) -> Path:
    """
    Ищет корень проекта по наличию configs/paths.yaml.
    Работает и из notebooks, и из scripts, и из src.
    """
    start_points = []

    if start is not None:
        start_points.append(Path(start).resolve())

    start_points.append(Path.cwd().resolve())
    start_points.append(Path(__file__).resolve())

    for start_point in start_points:
        candidates = [start_point, *start_point.parents]

        for candidate in candidates:
            if (candidate / "configs" / "paths.yaml").exists():
                return candidate

    raise FileNotFoundError(
        "Could not find project root. Expected configs/paths.yaml somewhere above."
    )


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise TypeError(f"YAML config must contain a dictionary: {path}")

    return data


def load_configs(
    config_dir: str | Path = "configs",
    project_root: str | Path | None = None,
) -> ProjectConfig:
    """
    Загружает основные конфиги проекта.

    Возвращает словарь вида:
    {
        "project_root": Path(...),
        "paths": ...,
        "data": ...
    }
    """
    root = find_project_root(Path(project_root)) if project_root else find_project_root()
    config_dir = root / config_dir

    return {
        "project_root": root,
        "paths": load_yaml(config_dir / "paths.yaml"),
        "data": load_yaml(config_dir / "data.yaml"),
    }


def resolve_project_path(config: ProjectConfig, relative_path: str | Path) -> Path:
    root = Path(config["project_root"])
    return (root / relative_path).resolve()


def get_path_from_config(config: ProjectConfig, section: str, key: str) -> Path:
    relative_path = config["paths"][section][key]
    return resolve_project_path(config, relative_path)


def _as_list(value: str | Iterable[str] | None) -> list[str] | None:
    if value is None:
        return None

    if isinstance(value, str):
        return [value]

    return list(value)


def _schema_names(lazy_frame: pl.LazyFrame) -> set[str]:
    try:
        return set(lazy_frame.collect_schema().names())
    except AttributeError:
        return set(lazy_frame.schema.keys())


def validate_columns(
    lazy_frame: pl.LazyFrame,
    expected_columns: Iterable[str],
    dataset_name: str,
) -> None:
    actual_columns = _schema_names(lazy_frame)
    expected_columns = set(expected_columns)

    missing_columns = expected_columns - actual_columns

    if missing_columns:
        raise ValueError(
            f"{dataset_name}: missing expected columns: {sorted(missing_columns)}. "
            f"Actual columns: {sorted(actual_columns)}"
        )


def find_parquet_payload_dir(
    base_dir: Path,
    payload_root_names: Iterable[str],
    parquet_glob: str,
) -> Path:
    """
    Находит папку, где реально лежит parquet-датасет.

    Важно: сначала проверяем ожидаемые payload-root папки,
    например user_actions_3_months, и только потом base_dir.

    Иначе для user_actions base_dir тоже содержит parquet-файлы рекурсивно,
    но date=* лежат не сразу в base_dir, а внутри user_actions_3_months.
    """
    candidates: list[Path] = []

    for root_name in payload_root_names:
        candidates.append(base_dir / root_name)

    candidates.append(base_dir)

    for candidate in candidates:
        if candidate.exists() and any(candidate.glob(parquet_glob)):
            return candidate

    checked = "\n".join(f"  - {candidate}" for candidate in candidates)

    raise FileNotFoundError(
        f"Could not find parquet payload directory.\n"
        f"Base directory: {base_dir}\n"
        f"Checked:\n{checked}"
    )


def list_event_dates(events_dir: Path) -> list[str]:
    """
    Возвращает даты из hive-partitioned структуры:

    date=2024-03-01/
    date=2024-03-02/
    ...
    """
    dates = []

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
    available_dates = list_event_dates(events_dir)

    selected_dates = _filter_dates(
        available_dates=available_dates,
        dates=dates,
        start_date=start_date,
        end_date=end_date,
        sample_days=sample_days,
    )

    action_types = _as_list(action_types)

    parquet_files: list[Path] = []

    for date in selected_dates:
        date_dir = events_dir / f"date={date}"

        if action_types is None:
            action_dirs = sorted(date_dir.glob("action_type=*"))
        else:
            action_dirs = [date_dir / f"action_type={action_type}" for action_type in action_types]

        for action_dir in action_dirs:
            if not action_dir.exists():
                continue

            parquet_files.extend(sorted(action_dir.glob("*.parquet")))

    if not parquet_files:
        raise FileNotFoundError(
            "No event parquet files found for selected filters. "
            f"events_dir={events_dir}, "
            f"dates={dates}, "
            f"start_date={start_date}, "
            f"end_date={end_date}, "
            f"sample_days={sample_days}, "
            f"action_types={action_types}"
        )

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
    """
    Лениво читает действия пользователей из parquet.

    Возвращает pl.LazyFrame, то есть данные не загружаются в память сразу.
    Это удобно для больших датасетов.
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
    """
    Загружает действия пользователей в память.

    По умолчанию грузит sample за 1 день, чтобы случайно не прочитать
    весь большой датасет.
    """
    effective_sample_days = sample_days if use_sample and dates is None else None

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
    parquet_files = sorted(products_dir.glob(parquet_glob))

    if not parquet_files:
        raise FileNotFoundError(f"No product parquet files found in: {products_dir}")

    return parquet_files


def scan_products(
    config: ProjectConfig | None = None,
    *,
    columns: Iterable[str] | None = None,
    validate: bool = True,
) -> pl.LazyFrame:
    """
    Лениво читает справочник товаров.
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
    """
    Загружает справочник товаров в память.
    """
    return scan_products(
        config=config,
        columns=columns,
        validate=validate,
    ).collect()