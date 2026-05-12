"""Preview the latest MVP recommendation artifacts.

This script is intentionally cross-platform: it works the same way in macOS,
Linux shells and Windows PowerShell/CMD.

Examples:
    uv run python scripts/preview_latest_recommendations.py
    uv run python scripts/preview_latest_recommendations.py --top-k 10
    uv run python scripts/preview_latest_recommendations.py --item-id 113
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import polars as pl

from ozon_similar_products.output.lookup import SimilarItemsLookup


def _load_manifest(path: Path) -> dict[str, Any]:
    """Load a JSON manifest file."""
    return json.loads(path.read_text(encoding="utf-8"))


def _path_from_manifest(
    manifest: Mapping[str, Any],
    manifest_path: Path,
    key: str,
) -> Path:
    """Resolve an artifact path stored in manifest."""
    paths = manifest.get("paths", {})
    path_value: Any = None

    if isinstance(paths, Mapping):
        path_value = paths.get(key)

    if path_value is None:
        path_value = manifest.get(key)

    if not isinstance(path_value, str) or not path_value:
        raise KeyError(f"Manifest does not contain path key: {key}")

    path = Path(path_value)
    if path.is_absolute():
        return path

    return (manifest_path.parent / path).resolve()


def _parse_item_id(value: str | None) -> int | str | None:
    """Parse CLI item_id.

    argparse receives all CLI values as strings. In our parquet outputs item_id is
    usually integer, so "113" must become 113; otherwise lookup will not find it.
    """
    if value is None:
        return None

    stripped = value.strip()
    if not stripped:
        return None

    if stripped.isdecimal() or (stripped.startswith("-") and stripped[1:].isdecimal()):
        return int(stripped)

    return stripped


def _print_manifest_summary(manifest: Mapping[str, Any]) -> None:
    """Print high-level manifest metadata and row counts."""
    print("RUN:", manifest.get("run_id"))
    print("WINDOW:", manifest.get("window_start"), "->", manifest.get("window_end"))
    print("SCORE:", manifest.get("score_method"))
    print("TOP_K:", manifest.get("top_k"))

    rows = manifest.get("rows", {})
    if isinstance(rows, Mapping):
        print("\nROWS:")
        for key, value in rows.items():
            print(f"{key}: {value}")


def _print_detailed_preview(detailed_path: Path, head: int) -> None:
    """Print a compact preview of detailed recommendations."""
    print("\nDETAILED RECOMMENDATIONS")
    print("file:", detailed_path)

    detailed = pl.read_parquet(detailed_path)
    print("shape:", detailed.shape)
    print("columns:", detailed.columns)

    preview_columns = [
        "item_id",
        "similar_item_id",
        "score",
        "rank",
        "source",
        "pair_count",
        "view_count",
        "click_count",
        "favorite_count",
        "to_cart_count",
        "unique_users",
        "unique_sessions",
    ]
    existing_columns = [column for column in preview_columns if column in detailed.columns]

    if detailed.is_empty():
        print("No detailed recommendations were generated.")
        return

    print(detailed.select(existing_columns).sort(["item_id", "rank"]).head(head))


def _print_compact_preview(compact_path: Path, head: int) -> pl.DataFrame:
    """Print a preview of compact lookup recommendations."""
    print("\nCOMPACT LOOKUP OUTPUT")
    print("file:", compact_path)

    compact = pl.read_parquet(compact_path)
    print("shape:", compact.shape)
    print("columns:", compact.columns)
    print(compact.head(head))
    return compact


def _select_item_id(compact: pl.DataFrame, item_id: int | str | None) -> Any:
    """Return requested item_id or the first item_id from compact output."""
    if item_id is not None:
        return item_id

    if compact.is_empty():
        return None

    return compact["item_id"][0]


def _print_compact_row(compact: pl.DataFrame, item_id: Any) -> None:
    """Print the compact row for the selected item_id when it exists."""
    if compact.is_empty():
        return

    row = compact.filter(pl.col("item_id") == item_id)
    if row.is_empty():
        print(f"item_id {item_id!r} was not found in compact output.")
        return

    print("\nCOMPACT ROW FOR SELECTED ITEM")
    print(row)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Preview latest recommendation artifacts and test SimilarItemsLookup.",
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=Path("outputs/recommendations/latest/manifest.json"),
        help="Path to latest manifest JSON.",
    )
    parser.add_argument(
        "--item-id",
        default=None,
        help="Optional item_id to query. If omitted, the first item_id from compact output is used.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of similar items to return from SimilarItemsLookup.",
    )
    parser.add_argument(
        "--head",
        type=int,
        default=20,
        help="Number of table rows to print in previews.",
    )
    return parser.parse_args()


def main() -> None:
    """Preview latest recommendation outputs and run a lookup query."""
    args = parse_args()

    manifest_path = args.manifest_path
    manifest = _load_manifest(manifest_path)

    _print_manifest_summary(manifest)

    detailed_path = _path_from_manifest(
        manifest=manifest,
        manifest_path=manifest_path,
        key="detailed_recommendations_path",
    )
    compact_path = _path_from_manifest(
        manifest=manifest,
        manifest_path=manifest_path,
        key="widget_recommendations_path",
    )

    _print_detailed_preview(detailed_path=detailed_path, head=args.head)
    compact = _print_compact_preview(compact_path=compact_path, head=args.head)

    print("\nLOOKUP")
    selected_item_id = _select_item_id(compact, _parse_item_id(args.item_id))
    if selected_item_id is None:
        print("No recommendations are available for lookup.")
        return

    _print_compact_row(compact, selected_item_id)

    lookup = SimilarItemsLookup(manifest_path)
    similar_items = lookup.get_similar_items(item_id=selected_item_id, top_k=args.top_k)

    print("item_id:", selected_item_id)
    print("similar_items:", similar_items)


if __name__ == "__main__":
    main()
