import polars as pl
import pytest

from ozon_similar_products.data.validation import (
    validate_pair_aggregates,
    validate_recommendations,
    validate_sessions,
)


def test_validate_sessions_raises_on_dtype_mismatch() -> None:
    frame = pl.DataFrame(
        {
            "user_id": ["1"],
            "session_id": ["s1"],
            "event_date": ["2024-01-01"],
            "timestamp": [pl.datetime(2024, 1, 1, 0, 0, 0)],
            "action_type": ["view"],
            "item_id": [1],
        }
    )

    with pytest.raises(ValueError, match="sessions.user_id: invalid dtype"):
        validate_sessions(frame)


def test_validate_pair_aggregates_raises_on_dtype_mismatch_lazy() -> None:
    frame = pl.DataFrame(
        {
            "item_id": [1],
            "similar_item_id": [2],
            "pair_count": [1],
            "view_count": [1],
            "click_count": [1],
            "favorite_count": [1],
            "to_cart_count": [1],
            "unique_users": [1],
            "unique_sessions": ["1"],
            "window_start": ["2024-01-01"],
            "window_end": ["2024-01-02"],
        }
    ).lazy()

    with pytest.raises(ValueError, match="pair_aggregates.unique_sessions: invalid dtype"):
        validate_pair_aggregates(frame)


def test_validate_recommendations_raises_on_dtype_mismatch() -> None:
    frame = pl.DataFrame(
        {
            "item_id": [1],
            "similar_item_id": [2],
            "score": [1],
            "rank": [1],
            "source": ["behavioral"],
        }
    )

    with pytest.raises(ValueError, match="recommendations.score: invalid dtype"):
        validate_recommendations(frame)
