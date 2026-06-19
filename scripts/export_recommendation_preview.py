"""Export a compact CSV/JSON recommendation preview for GitHub artifacts."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import polars as pl

from ozon_similar_products.output.manifest import load_manifest


def _path_from_manifest(
    manifest: Mapping[str, Any],
    manifest_path: Path,
    key: str,
) -> Path:
    paths = manifest.get("paths", {})
    path_value: Any = None
    if isinstance(paths, Mapping):
        path_value = paths.get(key)
    if path_value is None:
        path_value = manifest.get(key)
    if not isinstance(path_value, str) or not path_value:
        raise KeyError(f"Manifest does not contain path key: {key}")

    candidate = Path(path_value)
    if candidate.is_absolute():
        return candidate
    return (manifest_path.parent / candidate).resolve()


def _product_info(product_information_dir: Path) -> pl.LazyFrame | None:
    parquet_paths = sorted(
        path
        for path in product_information_dir.rglob("*.parquet")
        if path.is_file()
    )
    if not parquet_paths:
        return None

    return (
        pl.scan_parquet([path.as_posix() for path in parquet_paths])
        .select(["item_id", "name"])
        .unique(subset=["item_id"], keep="first")
    )


def _build_preview(
    detailed_path: Path,
    product_information_dir: Path,
    top_k: int,
    max_items: int,
) -> pl.DataFrame:
    detailed = pl.scan_parquet(detailed_path.as_posix())
    item_ids = detailed.select("item_id").unique(maintain_order=True).head(max_items)

    preview = (
        detailed.join(item_ids, on="item_id", how="semi")
        .filter(pl.col("rank") <= top_k)
        .sort(["item_id", "rank", "similar_item_id"])
    )

    product_info = _product_info(product_information_dir)
    if product_info is not None:
        items = product_info.rename({"name": "item_name"})
        similar_items = product_info.rename(
            {
                "item_id": "similar_item_id",
                "name": "similar_item_name",
            }
        )
        preview = preview.join(items, on="item_id", how="left")
        preview = preview.join(similar_items, on="similar_item_id", how="left")
    else:
        preview = preview.with_columns(
            pl.lit(None).alias("item_name"),
            pl.lit(None).alias("similar_item_name"),
        )

    optional_columns = [
        column
        for column in ["score", "source"]
        if column in preview.collect_schema().names()
    ]
    columns = [
        "item_id",
        "item_name",
        "similar_item_id",
        "similar_item_name",
        "rank",
        *optional_columns,
    ]
    return preview.select(columns).collect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a small recommendation preview CSV/JSON artifact.",
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=Path("outputs/latest/manifest.json"),
        help="Path to latest recommendation manifest.",
    )
    parser.add_argument(
        "--product-information-dir",
        type=Path,
        default=Path("data/raw/product_information"),
        help="Directory with product_information parquet files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/demo"),
        help="Directory for preview_recommendations.csv/json.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=20,
        help="Maximum rank to include for each item.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=25,
        help="Maximum number of source items to include.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.manifest_path)
    detailed_path = _path_from_manifest(
        manifest=manifest,
        manifest_path=args.manifest_path,
        key="detailed_recommendations_path",
    )

    preview = _build_preview(
        detailed_path=detailed_path,
        product_information_dir=args.product_information_dir,
        top_k=args.top_k,
        max_items=args.max_items,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "preview_recommendations.csv"
    json_path = args.output_dir / "preview_recommendations.json"
    metadata_path = args.output_dir / "preview_metadata.json"

    preview.write_csv(csv_path)
    preview.write_json(json_path)
    metadata_path.write_text(
        json.dumps(
            {
                "manifest_path": args.manifest_path.as_posix(),
                "detailed_recommendations_path": detailed_path.as_posix(),
                "product_information_dir": args.product_information_dir.as_posix(),
                "top_k": args.top_k,
                "max_items": args.max_items,
                "rows": preview.height,
                "columns": preview.columns,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    print(f"Wrote {csv_path} rows={preview.height}")
    print(f"Wrote {json_path}")
    print(f"Wrote {metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
