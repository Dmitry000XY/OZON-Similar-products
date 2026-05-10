"""Helpers for partitioned raw data layouts."""

from collections.abc import Iterable
from pathlib import Path


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


def filter_dates(
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


def collect_event_parquet_files(
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

    selected_dates = filter_dates(
        available_dates=available_dates,
        dates=dates,
        start_date=start_date,
        end_date=end_date,
        sample_days=sample_days,
    )

    parquet_files: list[Path] = []
    selected_action_types = list(action_types) if action_types is not None else None

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
