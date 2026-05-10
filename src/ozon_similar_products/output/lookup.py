"""Lookup helper for saved similar items."""

from pathlib import Path


class SimilarItemsLookup:
    """Read saved recommendations and return similar items."""

    def __init__(self, recommendations_path: str | Path) -> None:
        self.recommendations_path = Path(recommendations_path)

    def get_similar_items(
            self,
            item_id: int | str,
            top_k: int = 10,
    ) -> list[int | str]:
        """Return top-K similar items for item_id."""
        raise NotImplementedError
