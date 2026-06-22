"""Export recommendation graph artifacts from a recommendation parquet file."""

from __future__ import annotations

import argparse
from pathlib import Path

from ozon_similar_products.visualization import (
    RecommendationGraphConfig,
    export_recommendation_graph,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recommendation_path", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest-path", type=Path, default=None)
    parser.add_argument("--mode", choices=["overview", "ego"], default="overview")
    parser.add_argument("--selected-item-id", default=None)
    parser.add_argument("--max-rank", type=int, default=10)
    parser.add_argument("--max-edges", type=_optional_positive_int, default=2000)
    parser.add_argument("--max-nodes", type=_optional_positive_int, default=None)
    parser.add_argument("--ego-top-k", type=int, default=20)
    parser.add_argument("--second-hop-top-k", type=int, default=3)
    parser.add_argument(
        "--labels-mode",
        choices=["auto", "important", "all", "off"],
        default="important",
    )
    parser.add_argument("--theme", choices=["auto", "dark", "light"], default="auto")
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--exclude-fallback", action="store_true")
    parser.add_argument("--exclude-behavioral", action="store_true")
    parser.add_argument("--no-html", action="store_true")
    parser.add_argument("--no-json", action="store_true")
    parser.add_argument("--no-gexf", action="store_true")
    return parser.parse_args()


def _optional_positive_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        if value <= 0:
            raise argparse.ArgumentTypeError("value must be positive or All")
        return value
    normalized = value.strip().lower()
    if normalized in {"all", "none", "null", "unlimited"}:
        return None
    try:
        parsed = int(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer or All") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive or All")
    return parsed


def main() -> int:
    args = parse_args()
    config = RecommendationGraphConfig(
        mode=args.mode,
        selected_item_id=args.selected_item_id,
        max_rank=args.max_rank,
        max_edges=args.max_edges,
        max_nodes=args.max_nodes,
        ego_top_k=args.ego_top_k,
        second_hop_top_k=args.second_hop_top_k,
        labels_mode=args.labels_mode,
        theme=args.theme,
        include_behavioral=not args.exclude_behavioral,
        include_fallback=not args.exclude_fallback,
        min_score=args.min_score,
        export_html=not args.no_html,
        export_json=not args.no_json,
        export_gexf=not args.no_gexf,
    )
    result = export_recommendation_graph(
        recommendation_path=args.recommendation_path,
        output_dir=args.output_dir,
        config=config,
        manifest_path=args.manifest_path,
    )
    print(
        "Exported recommendation graph: "
        f"nodes={result.nodes_count} edges={result.edges_count} "
        f"manifest={result.manifest_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
