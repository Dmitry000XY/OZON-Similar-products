"""Tests for DataFrame validation helpers."""

import polars as pl
import pytest

from ozon_similar_products.data.validation import (
    validate_daily_pair_counts,
    validate_daily_pair_session_keys,
    validate_daily_pair_user_keys,
    validate_daily_pair_widget_counts,
)


def test_validate_daily_pair_counts_accepts_contract_columns() -> None:
    frame = pl.DataFrame(
        {
            "pair_date": [],
            "item_id": [],
            "similar_item_id": [],
            "pair_count": [],
            "view_count": [],
            "click_count": [],
            "favorite_count": [],
            "to_cart_count": [],
            "weighted_pair_count": [],
            "weighted_view_count": [],
            "weighted_click_count": [],
            "weighted_favorite_count": [],
            "weighted_to_cart_count": [],
        }
    )

    validate_daily_pair_counts(frame)


def test_validate_daily_pair_widget_counts_accepts_contract_columns() -> None:
    frame = pl.DataFrame(
        {
            "pair_date": [],
            "item_id": [],
            "similar_item_id": [],
            "target_widget_name": [],
            "pair_count": [],
            "view_count": [],
            "click_count": [],
            "favorite_count": [],
            "to_cart_count": [],
            "weighted_pair_count": [],
            "weighted_view_count": [],
            "weighted_click_count": [],
            "weighted_favorite_count": [],
            "weighted_to_cart_count": [],
        }
    )

    validate_daily_pair_widget_counts(frame)


def test_validate_daily_pair_user_keys_accepts_contract_columns() -> None:
    frame = pl.DataFrame(
        {
            "pair_date": [],
            "item_id": [],
            "similar_item_id": [],
            "user_id": [],
        }
    )

    validate_daily_pair_user_keys(frame)


def test_validate_daily_pair_session_keys_accepts_contract_columns() -> None:
    frame = pl.DataFrame(
        {
            "pair_date": [],
            "item_id": [],
            "similar_item_id": [],
            "user_id": [],
            "session_index": [],
        }
    )

    validate_daily_pair_session_keys(frame)


def test_validate_daily_pair_counts_rejects_missing_columns() -> None:
    frame = pl.DataFrame(
        {
            "pair_date": [],
            "item_id": [],
            "similar_item_id": [],
        }
    )

    with pytest.raises(ValueError, match="missing expected columns"):
        validate_daily_pair_counts(frame)


def test_validate_daily_pair_widget_counts_rejects_missing_columns() -> None:
    frame = pl.DataFrame(
        {
            "pair_date": [],
            "item_id": [],
            "similar_item_id": [],
        }
    )

    with pytest.raises(ValueError, match="missing expected columns"):
        validate_daily_pair_widget_counts(frame)


def test_validate_daily_pair_user_keys_rejects_missing_columns() -> None:
    frame = pl.DataFrame(
        {
            "pair_date": [],
            "item_id": [],
            "similar_item_id": [],
        }
    )

    with pytest.raises(ValueError, match="missing expected columns"):
        validate_daily_pair_user_keys(frame)


def test_validate_daily_pair_session_keys_rejects_missing_columns() -> None:
    frame = pl.DataFrame(
        {
            "pair_date": [],
            "item_id": [],
            "similar_item_id": [],
            "user_id": [],
        }
    )

    with pytest.raises(ValueError, match="missing expected columns"):
        validate_daily_pair_session_keys(frame)
