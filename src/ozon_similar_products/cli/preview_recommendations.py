"""CLI entrypoint for previewing latest recommendation artifacts."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import polars as pl

from ozon_similar_products.output.manifest import (
    find_compact_recommendations_path,
    find_manifest_path,
    load_manifest,
)
from ozon_similar_products.serving.lookup import SimilarItemsLookup


def _resolve_manifest_artifact_path(
    manifest: Mapping[str, Any],
    manifest_path: Path,
    *keys: str,
) -> Path:
    path_value = find_manifest_path(manifest, *keys)
    if not isinstance(path_value, str) or not path_value:
        raise KeyError(f"Manifest does not contain any path key: {keys}")
    return _resolve_path_value(path_value, manifest_path)


def _resolve_path_value(path_value: str, manifest_path: Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (manifest_path.parent / path).resolve()


def _parse_item_id(value: str | None) -> int | str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdecimal() or (stripped.startswith("-") and stripped[1:].isdecimal()):
        return int(stripped)
    return stripped


def _print_manifest_summary(manifest: Mapping[str, Any]) -> None:
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
    print("\nCOMPACT LOOKUP OUTPUT")
    print("file:", compact_path)

    compact = pl.read_parquet(compact_path)
    print("shape:", compact.shape)
    print("columns:", compact.columns)
    print(compact.head(head))
    return compact


def _select_item_id(compact: pl.DataFrame, item_id: int | str | None) -> Any:
    if item_id is not None:
        return item_id
    if compact.is_empty():
        return None
    return compact["item_id"][0]


def _print_compact_row(compact: pl.DataFrame, item_id: Any) -> None:
    if compact.is_empty():
        return

    row = compact.filter(pl.col("item_id") == item_id)
    if row.is_empty():
        print(f"item_id {item_id!r} was not found in compact output.")
        return

    print("\nCOMPACT ROW FOR SELECTED ITEM")
    print(row)


def parse_args() -> argparse.Namespace:
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


def main() -> int:
    """Preview latest recommendation outputs and run a lookup query."""
    pl.Config.set_tbl_formatting("ASCII_MARKDOWN")
    args = parse_args()

    manifest_path = args.manifest_path
    manifest = load_manifest(manifest_path)
    _print_manifest_summary(manifest)

    detailed_path = _resolve_manifest_artifact_path(
        manifest,
        manifest_path,
        "detailed_recommendations_path",
    )
    compact_manifest_path = find_compact_recommendations_path(manifest)
    if compact_manifest_path is None:
        raise KeyError("Manifest does not contain compact recommendations path")
    compact_path = _resolve_path_value(compact_manifest_path, manifest_path)

    _print_detailed_preview(detailed_path=detailed_path, head=args.head)
    compact = _print_compact_preview(compact_path=compact_path, head=args.head)

    print("\nLOOKUP")
    selected_item_id = _select_item_id(compact, _parse_item_id(args.item_id))
    if selected_item_id is None:
        print("No recommendations are available for lookup.")
        return 0

    _print_compact_row(compact, selected_item_id)

    lookup = SimilarItemsLookup(manifest_path)
    similar_items = lookup.get_similar_items(item_id=selected_item_id, top_k=args.top_k)

    print("item_id:", selected_item_id)
    print("similar_items:", similar_items)
    return 0
