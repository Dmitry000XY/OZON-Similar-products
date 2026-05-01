"""Validation helpers for raw and processed tables."""

from collections.abc import Iterable


def validate_columns(
    actual_columns: Iterable[str], expected_columns: Iterable[str]
) -> None:
    """Validate that all expected columns are present."""
    missing = set(expected_columns) - set(actual_columns)
    if missing:
        raise ValueError(f"Missing expected columns: {sorted(missing)}")
