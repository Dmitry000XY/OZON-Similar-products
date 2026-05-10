"""Session feasibility checks used by EDA."""

from collections.abc import Sequence

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


def _normalize_group_cols(group_cols: Sequence[str] | str | None, user_id_col: str) -> list[str]:
    if group_cols is None:
        return [user_id_col]
    if isinstance(group_cols, str):
        return [group_cols]
    return list(group_cols)


def add_session_markers(
    events: FrameLike,
    timeout_minutes: int = 30,
    user_id_col: str = "user_id",
    timestamp_col: str = "timestamp",
    group_cols: Sequence[str] | str | None = None,
    time_diff_col: str = "time_diff_seconds",
    new_session_col: str = "is_new_session",
    session_index_col: str = "session_index",
) -> pl.LazyFrame:
    """Sort events and add time-diff and session-start markers."""
    schema = _collect_schema(events)
    group_columns = _normalize_group_cols(group_cols, user_id_col)
    required_columns = {timestamp_col, *group_columns}
    missing_columns = required_columns - set(schema.keys())
    if missing_columns:
        raise ValueError(f"Missing columns for session checks: {sorted(missing_columns)}")

    timeout_seconds = timeout_minutes * 60
    sort_columns = [*group_columns, timestamp_col]

    return (
        _as_lazy(events)
        .filter(pl.all_horizontal([pl.col(column).is_not_null() for column in sort_columns]))
        .sort(sort_columns)
        .with_columns(
            pl.col(timestamp_col)
            .diff()
            .over(group_columns)
            .dt.total_seconds()
            .alias(time_diff_col)
        )
        .with_columns(
            (
                pl.col(time_diff_col).is_null() | (pl.col(time_diff_col) > timeout_seconds)
            )
            .cast(pl.Int64)
            .alias(new_session_col)
        )
        .with_columns(
            pl.col(new_session_col).cum_sum().over(group_columns).alias(session_index_col)
        )
    )


def _summary_exprs(
    timeout_minutes: int,
    time_diff_col: str,
    new_session_col: str,
    quantiles: Sequence[float],
) -> list[pl.Expr]:
    timeout_seconds = timeout_minutes * 60
    expressions: list[pl.Expr] = [
        pl.len().alias("events"),
        pl.col(time_diff_col).is_not_null().sum().alias("time_diffs"),
        (pl.col(time_diff_col) < 0).sum().alias("negative_time_diffs"),
        (pl.col(time_diff_col) == 0).sum().alias("zero_time_diffs"),
        (pl.col(time_diff_col) > timeout_seconds).sum().alias("gaps_over_timeout"),
        pl.col(new_session_col).sum().alias("sessions"),
        pl.lit(timeout_seconds).alias("timeout_seconds"),
    ]
    for quantile in quantiles:
        percentile = int(quantile * 100)
        expressions.append(pl.col(time_diff_col).quantile(quantile).alias(f"p{percentile}_seconds"))
    return expressions


def time_diff_summary(
    events: FrameLike,
    timeout_minutes: int = 30,
    user_id_col: str = "user_id",
    timestamp_col: str = "timestamp",
    group_cols: Sequence[str] | str | None = None,
    quantiles: Sequence[float] = (0.5, 0.75, 0.9, 0.95, 0.99),
) -> pl.DataFrame:
    """Return global time-gap summary after sorting events within each group."""
    markers = add_session_markers(
        events=events,
        timeout_minutes=timeout_minutes,
        user_id_col=user_id_col,
        timestamp_col=timestamp_col,
        group_cols=group_cols,
    )
    return markers.select(
        _summary_exprs(
            timeout_minutes=timeout_minutes,
            time_diff_col="time_diff_seconds",
            new_session_col="is_new_session",
            quantiles=quantiles,
        )
    ).collect()


def time_diff_summary_by_partition(
    events: FrameLike,
    partition_col: str,
    timeout_minutes: int = 30,
    user_id_col: str = "user_id",
    timestamp_col: str = "timestamp",
    group_cols: Sequence[str] | str | None = None,
    quantiles: Sequence[float] = (0.5, 0.75, 0.9, 0.95, 0.99),
) -> pl.DataFrame:
    """Return time-gap summary grouped by a partition column, usually date."""
    marker_group_cols = (
        [partition_col, user_id_col]
        if group_cols is None
        else _normalize_group_cols(group_cols, user_id_col)
    )
    markers = add_session_markers(
        events=events,
        timeout_minutes=timeout_minutes,
        user_id_col=user_id_col,
        timestamp_col=timestamp_col,
        group_cols=marker_group_cols,
    )
    return (
        markers.group_by(partition_col)
        .agg(
            _summary_exprs(
                timeout_minutes=timeout_minutes,
                time_diff_col="time_diff_seconds",
                new_session_col="is_new_session",
                quantiles=quantiles,
            )
        )
        .collect()
        .sort(partition_col)
    )
