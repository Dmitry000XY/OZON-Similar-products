"""Validation helpers for raw and processed tables."""

from collections.abc import Iterable

from ozon_similar_products.data import schemas
import polars as pl


def validate_columns(actual_columns: Iterable[str], expected_columns: Iterable[str]) -> None:
    """Validate that all expected columns are present."""
    missing = set(expected_columns) - set(actual_columns)
    if missing:
        raise ValueError(f"Missing expected columns: {sorted(missing)}")


def validate_frame_has_columns(frame: pl.DataFrame | pl.LazyFrame, expected_columns: list[str]) -> None:
    """Validate DataFrame/LazyFrame columns."""
    validate_columns(list(frame.columns), expected_columns)


def validate_raw_events(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.RAW_EVENTS_COLUMNS)


def validate_product_information(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.PRODUCT_INFORMATION_COLUMNS)


def validate_clean_events(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.CLEAN_EVENTS_COLUMNS)


def validate_sessions(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.SESSIONS_COLUMNS)


def validate_item_popularity(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.ITEM_POPULARITY_COLUMNS)


def validate_daily_item_pairs(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.DAILY_ITEM_PAIRS_COLUMNS)


def validate_pair_aggregates(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.PAIR_AGGREGATES_COLUMNS)


def validate_pair_scores(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.PAIR_SCORES_COLUMNS)


def validate_recommendations(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.RECOMMENDATIONS_COLUMNS)


def validate_widget_output(frame: pl.DataFrame | pl.LazyFrame) -> None:
    validate_frame_has_columns(frame, schemas.WIDGET_OUTPUT_COLUMNS)
