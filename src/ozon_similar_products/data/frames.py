"""Small DataFrame construction helpers shared by pipeline modules."""

from collections.abc import Sequence

import polars as pl


def empty_contract_frame(columns: Sequence[str]) -> pl.DataFrame:
    """Return an empty DataFrame with exactly the requested contract columns.

    The canonical list of required columns lives in ``data.schemas``. Modules should
    use those column lists instead of duplicating local typed schemas for every
    possible empty result. At the current MVP contract level validation checks the
    presence and order of columns; strict dtype validation can be added centrally
    later if the project needs it.
    """
    return pl.DataFrame({column: [] for column in columns}).select(list(columns))
