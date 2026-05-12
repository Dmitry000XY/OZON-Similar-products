"""Validation helpers for raw and processed tables."""

from collections.abc import Iterable

import polars as pl

from ozon_similar_products.data import schemas


def _frame_columns(frame: pl.DataFrame | pl.LazyFrame) -> list[str]:
    """Return column names from a Polars DataFrame or LazyFrame."""
    if isinstance(frame, pl.LazyFrame):
        try:
            return list(frame.collect_schema().names())
        except AttributeError:
            return list(frame.schema.keys())
    return list(frame.columns)


def _frame_schema(frame: pl.DataFrame | pl.LazyFrame) -> dict[str, pl.DataType]:
    """Return schema mapping for DataFrame or LazyFrame."""
    if isinstance(frame, pl.LazyFrame):
        return dict(frame.collect_schema())
    return frame.schema


def _as_lazy(frame: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    if isinstance(frame, pl.LazyFrame):
        return frame
    return frame.lazy()


def validate_columns(
        actual_columns: Iterable[str],
        expected_columns: Iterable[str],
        dataset_name: str | None = None,
) -> None:
    """Validate that all expected columns are present."""
    missing = set(expected_columns) - set(actual_columns)
    if missing:
        message = f"missing expected columns: {sorted(missing)}"
        if dataset_name:
            message = (
                f"{dataset_name}: {message}. "
                f"Actual columns: {sorted(actual_columns)}"
            )
        raise ValueError(message)


def validate_frame_has_columns(
        frame: pl.DataFrame | pl.LazyFrame,
        expected_columns: Iterable[str],
        dataset_name: str | None = None,
) -> None:
    """Validate DataFrame/LazyFrame columns."""
    validate_columns(
        _frame_columns(frame),
        expected_columns,
        dataset_name=dataset_name,
    )


def validate_frame_schema(
        frame: pl.DataFrame | pl.LazyFrame,
        expected_schema: dict[str, dict[str, object]],
        dataset_name: str,
) -> None:
    """Validate column presence, dtypes, and nullable policy."""
    validate_frame_has_columns(frame, expected_schema.keys(), dataset_name=dataset_name)
    actual_schema = _frame_schema(frame)

    for column, contract in expected_schema.items():
        expected_dtypes = contract["dtypes"]
        nullable = bool(contract["nullable"])
        actual_dtype = actual_schema[column]
        if not isinstance(expected_dtypes, tuple):
            raise TypeError(f"{dataset_name}.{column}: dtypes contract must be a tuple")
        if actual_dtype not in expected_dtypes:
            expected = [str(dtype) for dtype in expected_dtypes]
            raise ValueError(
                f"{dataset_name}.{column}: invalid dtype {actual_dtype}; expected one of {expected}"
            )

        if not nullable and _as_lazy(frame).select(pl.col(column).is_null().any()).collect().item():
            raise ValueError(f"{dataset_name}.{column}: null values are not allowed")


def validate_raw_events(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.RAW_EVENTS_COLUMNS)


def validate_product_information(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.PRODUCT_INFORMATION_COLUMNS)


def validate_clean_events(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.CLEAN_EVENTS_COLUMNS)


def validate_sessions(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_schema(frame, schemas.SESSIONS_SCHEMA, "sessions")


def validate_item_popularity(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.ITEM_POPULARITY_COLUMNS)


def validate_action_type_distribution(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.ACTION_TYPE_DISTRIBUTION_COLUMNS)


def validate_daily_item_pairs(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.DAILY_ITEM_PAIRS_COLUMNS)


def validate_pair_aggregates(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_schema(frame, schemas.PAIR_AGGREGATES_SCHEMA, "pair_aggregates")


def validate_pair_scores(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.PAIR_SCORES_COLUMNS)


def validate_recommendations(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_schema(frame, schemas.RECOMMENDATIONS_SCHEMA, "recommendations")


def validate_widget_output(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.WIDGET_OUTPUT_COLUMNS)
