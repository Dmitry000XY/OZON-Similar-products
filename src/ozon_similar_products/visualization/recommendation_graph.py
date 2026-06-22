"""Build and export small recommendation graphs for demo runs."""

from __future__ import annotations

import json
import math
import os
import shutil
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree as ET

import polars as pl

_REQUIRED_COLUMNS = (
    "item_id",
    "similar_item_id",
    "rank",
    "score",
    "source",
    "item_name",
    "similar_item_name",
)
_OPTIONAL_NODE_FIELDS = ("category_name", "category", "type", "brand", "category_type")
_FALLBACK_SOURCES = {
    "fallback_category_type_popular",
    "fallback_category_popular",
    "fallback_type_popular",
    "fallback_brand_popular",
    "fallback_global_popular",
}
_SOURCE_GROUP_COLORS = {
    "behavioral": "#159895",
    "category_fallback": "#f59e0b",
    "brand_fallback": "#c026d3",
    "popular_fallback": "#ef4444",
    "fallback": "#64748b",
    "unknown": "#cbd5e1",
}
_CENTER_COLOR = "#4f46e5"


@dataclass(frozen=True)
class RecommendationGraphConfig:
    """Controls graph size, filtering, and export formats."""

    mode: Literal["overview", "ego"] = "overview"
    selected_item_id: str | int | None = None
    max_rank: int = 10
    max_edges: int | None = 2000
    max_nodes: int | None = None
    ego_top_k: int = 20
    second_hop_top_k: int = 3
    labels_mode: Literal["auto", "important", "all", "off"] = "important"
    theme: Literal["auto", "dark", "light"] = "auto"
    include_behavioral: bool = True
    include_fallback: bool = True
    min_score: float | None = None
    export_html: bool = True
    export_json: bool = True
    export_gexf: bool = True


@dataclass(frozen=True)
class RecommendationGraphData:
    """Serializable graph data for JSON, HTML, and GEXF writers."""

    metadata: dict[str, Any]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


@dataclass(frozen=True)
class GraphExportResult:
    """Paths and basic counts for exported graph artifacts."""

    html_path: Path | None
    json_path: Path | None
    gexf_path: Path | None
    manifest_path: Path
    nodes_count: int
    edges_count: int


def build_recommendation_graph(
    recommendations: pl.DataFrame,
    config: RecommendationGraphConfig,
) -> RecommendationGraphData:
    """Build a bounded directed graph from recommendation rows."""

    _validate_config(config)
    normalized = _normalize_recommendations(recommendations)
    filtered = _filter_recommendations(normalized, config)

    if config.mode == "ego":
        selected_edges = _select_ego_edges(filtered, config)
    else:
        selected_edges = _select_overview_edges(filtered, config)

    edge_rows = selected_edges.to_dicts()
    if config.max_edges is not None and len(edge_rows) > config.max_edges:
        edge_rows = edge_rows[: config.max_edges]

    edge_rows = _drop_edges_for_node_limit(edge_rows, config)
    nodes = _build_nodes(edge_rows, config)
    edges = [_edge_payload(index, row) for index, row in enumerate(edge_rows)]
    _apply_layout(nodes, edges, config)
    _apply_label_visibility(nodes, config)

    metadata = {
        **asdict(config),
        "nodes_count": len(nodes),
        "edges_count": len(edges),
        "source_groups": dict(_SOURCE_GROUP_COLORS),
    }
    return RecommendationGraphData(metadata=metadata, nodes=nodes, edges=edges)


def export_recommendation_graph(
    recommendation_path: Path,
    output_dir: Path,
    config: RecommendationGraphConfig,
    *,
    manifest_path: Path | None = None,
) -> GraphExportResult:
    """Build and write recommendation graph artifacts for one run."""

    recommendations = pl.read_parquet(recommendation_path)
    graph = build_recommendation_graph(recommendations, config)

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "ego_graph" if config.mode == "ego" else "recommendations_graph"
    html_path = output_dir / f"{stem}.html" if config.export_html else None
    json_path = output_dir / f"{stem}.json" if config.export_json else None
    gexf_path = output_dir / f"{stem}.gexf" if config.export_gexf else None

    if json_path is not None:
        _write_json(json_path, _graph_json_payload(graph))
    if gexf_path is not None:
        _write_gexf(gexf_path, graph)
    if html_path is not None:
        html_path.write_text(_render_html(graph), encoding="utf-8")
        if config.mode == "overview":
            compatibility_path = output_dir.parent / "graph.html"
            shutil.copy2(html_path, compatibility_path)

    graph_manifest_path = output_dir / "manifest.json"
    manifest = {
        "artifact_type": "recommendation_graph",
        "mode": config.mode,
        "recommendation_path": _path_for_manifest(recommendation_path, graph_manifest_path.parent),
        "source_manifest_path": (
            _path_for_manifest(manifest_path, graph_manifest_path.parent)
            if manifest_path is not None
            else None
        ),
        "created_at": datetime.now(UTC).isoformat(),
        "nodes_count": len(graph.nodes),
        "edges_count": len(graph.edges),
        "config": asdict(config),
        "paths": {
            key: path.name
            for key, path in {
                "html": html_path,
                "json": json_path,
                "gexf": gexf_path,
            }.items()
            if path is not None
        },
    }
    _write_json(graph_manifest_path, manifest)

    return GraphExportResult(
        html_path=html_path,
        json_path=json_path,
        gexf_path=gexf_path,
        manifest_path=graph_manifest_path,
        nodes_count=len(graph.nodes),
        edges_count=len(graph.edges),
    )


def _validate_config(config: RecommendationGraphConfig) -> None:
    if config.mode not in {"overview", "ego"}:
        raise ValueError("graph mode must be overview or ego")
    if config.max_edges is not None and config.max_edges <= 0:
        raise ValueError("max_edges must be positive")
    if config.max_nodes is not None and config.max_nodes <= 0:
        raise ValueError("max_nodes must be positive")
    if config.max_rank <= 0:
        raise ValueError("max_rank must be positive")
    if config.ego_top_k <= 0:
        raise ValueError("ego_top_k must be positive")
    if config.second_hop_top_k < 0:
        raise ValueError("second_hop_top_k must be non-negative")
    if config.labels_mode not in {"auto", "important", "all", "off"}:
        raise ValueError("labels_mode must be auto, important, all, or off")
    if config.theme not in {"auto", "dark", "light"}:
        raise ValueError("theme must be auto, dark, or light")


def _normalize_recommendations(recommendations: pl.DataFrame) -> pl.DataFrame:
    normalized = recommendations.clone()
    for column in _REQUIRED_COLUMNS:
        if column not in normalized.columns:
            normalized = normalized.with_columns(pl.lit(None).alias(column))

    return normalized.with_columns(
        pl.col("item_id").cast(pl.Utf8),
        pl.col("similar_item_id").cast(pl.Utf8),
        pl.col("item_name").cast(pl.Utf8),
        pl.col("similar_item_name").cast(pl.Utf8),
        pl.col("source").cast(pl.Utf8).fill_null("unknown"),
        pl.col("score").cast(pl.Float64).fill_null(0.0),
        pl.col("rank").cast(pl.Int64).fill_null(2**31 - 1),
    ).filter(pl.col("item_id").is_not_null() & pl.col("similar_item_id").is_not_null())


def _filter_recommendations(
    recommendations: pl.DataFrame,
    config: RecommendationGraphConfig,
) -> pl.DataFrame:
    frame = recommendations.filter(pl.col("rank") <= config.max_rank)
    if config.min_score is not None:
        frame = frame.filter(pl.col("score") >= config.min_score)
    if not config.include_fallback:
        frame = frame.filter(pl.col("source") == "behavioral")
    if not config.include_behavioral:
        frame = frame.filter(pl.col("source") != "behavioral")
    return _sort_for_graph(frame)


def _sort_for_graph(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    return (
        frame.with_columns(
            pl.when(pl.col("source") == "behavioral").then(0).otherwise(1).alias("_source_priority")
        )
        .sort(
            ["_source_priority", "score", "rank", "item_id", "similar_item_id"],
            descending=[False, True, False, False, False],
        )
        .drop("_source_priority")
    )


def _select_overview_edges(
    recommendations: pl.DataFrame,
    config: RecommendationGraphConfig,
) -> pl.DataFrame:
    return recommendations if config.max_edges is None else recommendations.head(config.max_edges)


def _select_ego_edges(
    recommendations: pl.DataFrame,
    config: RecommendationGraphConfig,
) -> pl.DataFrame:
    if config.selected_item_id is None:
        return recommendations.head(0)

    center_id = str(config.selected_item_id)
    first_hop = recommendations.filter(pl.col("item_id") == center_id).head(config.ego_top_k)
    neighbor_ids = (
        first_hop.get_column("similar_item_id").to_list() if not first_hop.is_empty() else []
    )
    second_hop_frames = []
    for neighbor_id in neighbor_ids:
        second_hop = recommendations.filter(pl.col("item_id") == str(neighbor_id)).head(
            config.second_hop_top_k
        )
        if not second_hop.is_empty():
            second_hop_frames.append(second_hop)

    frames = [first_hop, *second_hop_frames]
    non_empty = [frame for frame in frames if not frame.is_empty()]
    if not non_empty:
        return recommendations.head(0)

    selected = (
        _sort_for_graph(pl.concat(non_empty, how="vertical")).unique(
            subset=["item_id", "similar_item_id"],
            keep="first",
        )
    )
    return selected if config.max_edges is None else selected.head(config.max_edges)


def _drop_edges_for_node_limit(
    edge_rows: list[dict[str, Any]],
    config: RecommendationGraphConfig,
) -> list[dict[str, Any]]:
    if config.max_nodes is None:
        return edge_rows

    node_ids = {str(row["item_id"]) for row in edge_rows}
    node_ids.update(str(row["similar_item_id"]) for row in edge_rows)
    if len(node_ids) <= config.max_nodes:
        return edge_rows

    out_degree: dict[str, int] = defaultdict(int)
    in_degree: dict[str, int] = defaultdict(int)
    score_sum: dict[str, float] = defaultdict(float)
    for row in edge_rows:
        source = str(row["item_id"])
        target = str(row["similar_item_id"])
        score = _float_or_zero(row.get("score"))
        out_degree[source] += 1
        in_degree[target] += 1
        score_sum[source] += score
        score_sum[target] += score

    selected = sorted(
        node_ids,
        key=lambda node_id: (
            out_degree[node_id],
            out_degree[node_id] + in_degree[node_id],
            score_sum[node_id],
            node_id,
        ),
        reverse=True,
    )[: config.max_nodes]
    selected_ids = set(selected)
    return [
        row
        for row in edge_rows
        if str(row["item_id"]) in selected_ids and str(row["similar_item_id"]) in selected_ids
    ]


def _build_nodes(
    edge_rows: list[dict[str, Any]],
    config: RecommendationGraphConfig,
) -> list[dict[str, Any]]:
    out_degree: dict[str, int] = defaultdict(int)
    in_degree: dict[str, int] = defaultdict(int)
    names: dict[str, str | None] = {}
    extra_fields: dict[str, dict[str, Any]] = defaultdict(dict)

    for row in edge_rows:
        source = str(row["item_id"])
        target = str(row["similar_item_id"])
        out_degree[source] += 1
        in_degree[target] += 1
        _set_first(names, source, row.get("item_name"))
        _set_first(names, target, row.get("similar_item_name"))
        _collect_optional_node_fields(extra_fields[source], row, prefix="")
        _collect_optional_node_fields(extra_fields[target], row, prefix="similar_")

    if config.mode == "ego" and config.selected_item_id is not None:
        selected_id = str(config.selected_item_id)
        out_degree.setdefault(selected_id, 0)
        in_degree.setdefault(selected_id, 0)

    node_ids = sorted(
        set(out_degree) | set(in_degree),
        key=lambda node_id: (
            -(out_degree[node_id] + in_degree[node_id]),
            -out_degree[node_id],
            node_id,
        ),
    )

    nodes: list[dict[str, Any]] = []
    selected_id = str(config.selected_item_id) if config.selected_item_id is not None else None
    for node_id in node_ids:
        item_name = names.get(node_id)
        degree = out_degree[node_id] + in_degree[node_id]
        node = {
            "id": node_id,
            "label": item_name or node_id,
            "item_id": node_id,
            "item_name": item_name,
            "recommendation_count": out_degree[node_id],
            "in_degree": in_degree[node_id],
            "out_degree": out_degree[node_id],
            "degree": degree,
            "is_center": selected_id == node_id,
        }
        node.update(extra_fields.get(node_id, {}))
        nodes.append(node)
    return nodes


def _edge_payload(index: int, row: dict[str, Any]) -> dict[str, Any]:
    source = str(row["item_id"])
    target = str(row["similar_item_id"])
    source_type = str(row.get("source") or "unknown")
    score = _float_or_zero(row.get("score"))
    rank = _int_or_none(row.get("rank"))
    source_group = recommendation_source_group(source_type)
    return {
        "id": f"e{index}",
        "source": source,
        "target": target,
        "weight": score,
        "score": score,
        "rank": rank,
        "source_type": source_type,
        "recommendation_source": source_type,
        "source_group": source_group,
        "color": _SOURCE_GROUP_COLORS.get(source_group, _SOURCE_GROUP_COLORS["unknown"]),
    }


def _apply_layout(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    config: RecommendationGraphConfig,
) -> None:
    if not nodes:
        return

    width = 1200.0
    height = 860.0
    center_x = width / 2
    center_y = height / 2
    node_by_id = {str(node["id"]): node for node in nodes}
    max_degree = max(1, *(int(node.get("degree") or 0) for node in nodes))
    center_id = str(config.selected_item_id) if config.selected_item_id is not None else None

    if config.mode == "ego" and center_id is not None:
        _apply_ego_layout(nodes, edges, center_id, center_x, center_y, max_degree)
    else:
        for index, node in enumerate(nodes):
            degree_ratio = (int(node.get("degree") or 0) + 1) / (max_degree + 1)
            angle = (index / max(1, len(nodes))) * math.tau
            radius = 90 + (1 - degree_ratio) * 330 + (index % 11) * 11
            node["x"] = center_x + math.cos(angle) * radius
            node["y"] = center_y + math.sin(angle) * radius

    if len(nodes) <= 320:
        _relax_layout(nodes, edges, node_by_id, width, height, center_id)

    for node in nodes:
        node["x"] = round(_clamp(_float_or_zero(node.get("x")), 24, width - 24), 2)
        node["y"] = round(_clamp(_float_or_zero(node.get("y")), 24, height - 24), 2)


def _apply_ego_layout(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    center_id: str,
    center_x: float,
    center_y: float,
    max_degree: int,
) -> None:
    first_hop = {str(edge["target"]) for edge in edges if str(edge["source"]) == center_id}
    first_hop.add(center_id)
    for index, node in enumerate(nodes):
        node_id = str(node["id"])
        if node_id == center_id:
            node["x"] = center_x
            node["y"] = center_y
            continue
        degree_ratio = (int(node.get("degree") or 0) + 1) / (max_degree + 1)
        angle = (index / max(1, len(nodes) - 1)) * math.tau
        radius_base = 205 if node_id in first_hop else 345
        radius = radius_base + (1 - degree_ratio) * 70 + (index % 5) * 12
        node["x"] = center_x + math.cos(angle) * radius
        node["y"] = center_y + math.sin(angle) * radius


def _relax_layout(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
    width: float,
    height: float,
    center_id: str | None,
) -> None:
    area = width * height
    ideal_distance = math.sqrt(area / max(1, len(nodes))) * 0.55
    velocities = {str(node["id"]): [0.0, 0.0] for node in nodes}

    for _ in range(80):
        for index, left in enumerate(nodes):
            left_id = str(left["id"])
            for right in nodes[index + 1 :]:
                right_id = str(right["id"])
                dx = _float_or_zero(left.get("x")) - _float_or_zero(right.get("x"))
                dy = _float_or_zero(left.get("y")) - _float_or_zero(right.get("y"))
                distance_sq = max(dx * dx + dy * dy, 16.0)
                force = min(5.0, (ideal_distance * ideal_distance) / distance_sq)
                distance = math.sqrt(distance_sq)
                fx = (dx / distance) * force
                fy = (dy / distance) * force
                velocities[left_id][0] += fx
                velocities[left_id][1] += fy
                velocities[right_id][0] -= fx
                velocities[right_id][1] -= fy

        for edge in edges:
            source = node_by_id.get(str(edge["source"]))
            target = node_by_id.get(str(edge["target"]))
            if source is None or target is None:
                continue
            source_id = str(source["id"])
            target_id = str(target["id"])
            dx = _float_or_zero(target.get("x")) - _float_or_zero(source.get("x"))
            dy = _float_or_zero(target.get("y")) - _float_or_zero(source.get("y"))
            distance = max(math.sqrt(dx * dx + dy * dy), 1.0)
            force = min(3.5, (distance - ideal_distance) * 0.018)
            fx = (dx / distance) * force
            fy = (dy / distance) * force
            velocities[source_id][0] += fx
            velocities[source_id][1] += fy
            velocities[target_id][0] -= fx
            velocities[target_id][1] -= fy

        for node in nodes:
            node_id = str(node["id"])
            if center_id is not None and node_id == center_id:
                node["x"] = width / 2
                node["y"] = height / 2
                velocities[node_id] = [0.0, 0.0]
                continue
            vx, vy = velocities[node_id]
            node["x"] = _clamp(_float_or_zero(node.get("x")) + vx * 0.55, 28, width - 28)
            node["y"] = _clamp(_float_or_zero(node.get("y")) + vy * 0.55, 28, height - 28)
            velocities[node_id] = [vx * 0.62, vy * 0.62]


def _apply_label_visibility(
    nodes: list[dict[str, Any]],
    config: RecommendationGraphConfig,
) -> None:
    if config.labels_mode == "off":
        for node in nodes:
            node["label_visible"] = False
        return
    if config.labels_mode == "all":
        for node in nodes:
            node["label_visible"] = True
        return

    label_limit = 80 if config.mode == "ego" else 45
    important_ids = {
        str(node["id"])
        for node in sorted(
            nodes,
            key=lambda node: (
                bool(node.get("is_center")),
                int(node.get("degree") or 0),
                int(node.get("out_degree") or 0),
                str(node.get("id")),
            ),
            reverse=True,
        )[:label_limit]
    }
    for node in nodes:
        node["label_visible"] = bool(node.get("is_center")) or str(node["id"]) in important_ids


def recommendation_source_group(source: str | None) -> str:
    """Group detailed recommendation source labels for visualization colors."""

    if source == "behavioral":
        return "behavioral"
    if source in {
        "fallback_category_type_popular",
        "fallback_category_popular",
        "fallback_type_popular",
    }:
        return "category_fallback"
    if source == "fallback_brand_popular":
        return "brand_fallback"
    if source == "fallback_global_popular":
        return "popular_fallback"
    if source in _FALLBACK_SOURCES or str(source or "").startswith("fallback"):
        return "fallback"
    return "unknown"


def _set_first(target: dict[str, str | None], key: str, value: Any) -> None:
    if target.get(key):
        return
    if value is None:
        target.setdefault(key, None)
        return
    text = str(value)
    target[key] = text if text else None


def _collect_optional_node_fields(
    target: dict[str, Any],
    row: dict[str, Any],
    *,
    prefix: str,
) -> None:
    for field in _OPTIONAL_NODE_FIELDS:
        key = f"{prefix}{field}"
        if field not in target and key in row:
            target[field] = _json_value(row.get(key))


def _graph_json_payload(graph: RecommendationGraphData) -> dict[str, Any]:
    return {
        "metadata": graph.metadata,
        "nodes": graph.nodes,
        "edges": graph.edges,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_gexf(path: Path, graph: RecommendationGraphData) -> None:
    ns = "http://www.gexf.net/1.2draft"
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}gexf", {"version": "1.2"})
    graph_node = ET.SubElement(
        root, f"{{{ns}}}graph", {"mode": "static", "defaultedgetype": "directed"}
    )

    node_attrs = ET.SubElement(graph_node, f"{{{ns}}}attributes", {"class": "node"})
    for attr_id, title, attr_type in (
        ("item_id", "item_id", "string"),
        ("item_name", "item_name", "string"),
        ("recommendation_count", "recommendation_count", "integer"),
        ("in_degree", "in_degree", "integer"),
        ("out_degree", "out_degree", "integer"),
        ("degree", "degree", "integer"),
        ("is_center", "is_center", "boolean"),
        ("label_visible", "label_visible", "boolean"),
        ("x", "x", "double"),
        ("y", "y", "double"),
    ):
        ET.SubElement(
            node_attrs, f"{{{ns}}}attribute", {"id": attr_id, "title": title, "type": attr_type}
        )

    edge_attrs = ET.SubElement(graph_node, f"{{{ns}}}attributes", {"class": "edge"})
    for attr_id, title, attr_type in (
        ("score", "score", "double"),
        ("rank", "rank", "integer"),
        ("source", "source", "string"),
        ("source_group", "source_group", "string"),
        ("color", "color", "string"),
    ):
        ET.SubElement(
            edge_attrs, f"{{{ns}}}attribute", {"id": attr_id, "title": title, "type": attr_type}
        )

    nodes_node = ET.SubElement(graph_node, f"{{{ns}}}nodes")
    for node in graph.nodes:
        item = ET.SubElement(
            nodes_node,
            f"{{{ns}}}node",
            {"id": str(node["id"]), "label": str(node.get("label") or node["id"])},
        )
        values = ET.SubElement(item, f"{{{ns}}}attvalues")
        for attr_id in (
            "item_id",
            "item_name",
            "recommendation_count",
            "in_degree",
            "out_degree",
            "degree",
            "is_center",
            "label_visible",
            "x",
            "y",
        ):
            ET.SubElement(
                values,
                f"{{{ns}}}attvalue",
                {"for": attr_id, "value": _gexf_attr_value(node.get(attr_id))},
            )

    edges_node = ET.SubElement(graph_node, f"{{{ns}}}edges")
    for edge in graph.edges:
        item = ET.SubElement(
            edges_node,
            f"{{{ns}}}edge",
            {
                "id": str(edge["id"]),
                "source": str(edge["source"]),
                "target": str(edge["target"]),
                "weight": str(edge.get("weight", 0.0)),
            },
        )
        values = ET.SubElement(item, f"{{{ns}}}attvalues")
        for attr_id, key in (
            ("score", "score"),
            ("rank", "rank"),
            ("source", "recommendation_source"),
            ("source_group", "source_group"),
            ("color", "color"),
        ):
            ET.SubElement(
                values,
                f"{{{ns}}}attvalue",
                {"for": attr_id, "value": _gexf_attr_value(edge.get(key))},
            )

    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _render_html(graph: RecommendationGraphData) -> str:
    payload = json.dumps(_graph_json_payload(graph), ensure_ascii=False)
    return _HTML_TEMPLATE.replace("__GRAPH_DATA__", payload)


def _path_for_manifest(path: Path, base_dir: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return Path(os.path.relpath(resolved, base_dir.resolve())).as_posix()


def _json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if hasattr(value, "item"):
        return _json_value(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _gexf_attr_value(value: Any) -> str:
    normalized = _json_value(value)
    if normalized is None:
        return ""
    if isinstance(normalized, bool):
        return str(normalized).lower()
    return str(normalized)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _float_or_zero(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Recommendation graph</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --surface: #ffffff;
      --surface-2: #eef2f7;
      --ink: #142033;
      --muted: #5f6f85;
      --line: #d6dde8;
      --shadow: rgba(15, 23, 42, 0.12);
      --label-stroke: rgba(255, 255, 255, 0.9);
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #090d14;
        --surface: #111827;
        --surface-2: #182235;
        --ink: #edf2f7;
        --muted: #a6b3c4;
        --line: #2f3b4e;
        --shadow: rgba(0, 0, 0, 0.35);
        --label-stroke: rgba(8, 13, 21, 0.9);
      }
    }
    body.theme-light {
      --bg: #f5f7fb;
      --surface: #ffffff;
      --surface-2: #eef2f7;
      --ink: #142033;
      --muted: #5f6f85;
      --line: #d6dde8;
      --shadow: rgba(15, 23, 42, 0.12);
      --label-stroke: rgba(255, 255, 255, 0.9);
    }
    body.theme-dark {
      --bg: #090d14;
      --surface: #111827;
      --surface-2: #182235;
      --ink: #edf2f7;
      --muted: #a6b3c4;
      --line: #2f3b4e;
      --shadow: rgba(0, 0, 0, 0.35);
      --label-stroke: rgba(8, 13, 21, 0.9);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .toolbar {
      display: grid;
      grid-template-columns: minmax(180px, 1fr) minmax(220px, 380px) minmax(150px, auto) auto auto;
      align-items: center;
      gap: 10px;
      padding: 12px 14px;
      background: color-mix(in srgb, var(--surface) 94%, transparent);
      border-bottom: 1px solid var(--line);
      box-shadow: 0 8px 30px var(--shadow);
      position: sticky;
      top: 0;
      z-index: 4;
      backdrop-filter: blur(10px);
    }
    .title {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
      font-weight: 700;
    }
    .title small {
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    input, select, button {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--ink);
      font: inherit;
      min-height: 36px;
    }
    input, select { padding: 7px 10px; }
    button {
      cursor: pointer;
      padding: 7px 12px;
      font-weight: 650;
    }
    button:hover, input:focus, select:focus {
      outline: 2px solid color-mix(in srgb, #38bdf8 38%, transparent);
      outline-offset: 1px;
    }
    .counts {
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
      text-align: right;
    }
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 9px 14px;
      background: color-mix(in srgb, var(--surface-2) 72%, transparent);
      border-bottom: 1px solid var(--line);
    }
    .legend span {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
    }
    .swatch { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
    #canvas {
      width: 100%;
      height: calc(100vh - 96px);
      min-height: 760px;
      background:
        radial-gradient(circle at center, color-mix(in srgb, var(--surface-2) 74%, transparent), transparent 66%),
        var(--bg);
      display: block;
      cursor: grab;
      touch-action: none;
    }
    #canvas.dragging { cursor: grabbing; }
    .edge {
      stroke-opacity: 0.2;
      vector-effect: non-scaling-stroke;
      transition: opacity 120ms ease, stroke-opacity 120ms ease;
    }
    .node {
      stroke: color-mix(in srgb, var(--surface) 88%, var(--ink));
      stroke-width: 1.4;
      cursor: pointer;
      vector-effect: non-scaling-stroke;
      transition: opacity 120ms ease, stroke-width 120ms ease;
    }
    .node.center { stroke: #f8fafc; stroke-width: 3; }
    .node.dimmed, .edge.dimmed, .label.dimmed { opacity: 0.1; }
    .node.highlight { stroke: #38bdf8; stroke-width: 4; opacity: 1; }
    .edge.highlight { stroke-opacity: 0.72; }
    .label {
      fill: var(--ink);
      font-size: 10.5px;
      font-weight: 560;
      paint-order: stroke;
      stroke: var(--label-stroke);
      stroke-width: 3px;
      stroke-linejoin: round;
      pointer-events: none;
    }
    .label.hidden { display: none; }
    #tooltip {
      position: fixed;
      pointer-events: none;
      background: rgba(8, 13, 21, 0.95);
      color: white;
      border: 1px solid rgba(148, 163, 184, 0.28);
      border-radius: 7px;
      padding: 9px 10px;
      font-size: 12px;
      line-height: 1.38;
      max-width: 310px;
      display: none;
      z-index: 5;
      box-shadow: 0 14px 34px rgba(0, 0, 0, 0.28);
    }
    @media (max-width: 760px) {
      .toolbar { grid-template-columns: 1fr; align-items: stretch; }
      .counts { text-align: left; }
      #canvas { height: 820px; }
    }
  </style>
</head>
<body>
  <div class="toolbar">
    <div class="title">
      <span>Recommendation graph</span>
      <small id="subtitle"></small>
    </div>
    <input id="search" type="search" placeholder="Search item_id or name">
    <select id="labels-mode" aria-label="Label mode">
      <option value="auto">Auto labels</option>
      <option value="important">Important labels</option>
      <option value="all">All labels</option>
      <option value="off">No labels</option>
    </select>
    <button id="reset-view" type="button">Reset</button>
    <span class="counts" id="counts"></span>
  </div>
  <div class="legend" id="legend"></div>
  <svg id="canvas" viewBox="0 0 1200 860" role="img" aria-label="Recommendation graph">
    <g id="viewport"></g>
  </svg>
  <div id="tooltip"></div>
  <script type="application/json" id="graph-data">__GRAPH_DATA__</script>
  <script>
    const graph = JSON.parse(document.getElementById("graph-data").textContent);
    const svg = document.getElementById("canvas");
    const viewport = document.getElementById("viewport");
    const tooltip = document.getElementById("tooltip");
    const counts = document.getElementById("counts");
    const subtitle = document.getElementById("subtitle");
    const search = document.getElementById("search");
    const resetButton = document.getElementById("reset-view");
    const labelsMode = document.getElementById("labels-mode");
    const width = 1200;
    const height = 860;
    const nodes = graph.nodes.map((node, index) => ({...node, index}));
    const edges = graph.edges;
    const byId = new Map(nodes.map(node => [String(node.id), node]));
    const neighbors = new Map(nodes.map(node => [String(node.id), new Set([String(node.id)])]));
    let transform = {x: 0, y: 0, k: 1};
    let dragStart = null;

    applyTheme(graph.metadata.theme || "auto");
    labelsMode.value = graph.metadata.labels_mode || "important";
    counts.textContent = `${nodes.length} nodes / ${edges.length} edges`;
    subtitle.textContent = `${graph.metadata.mode || "overview"} · max_rank=${graph.metadata.max_rank}`;

    edges.forEach(edge => {
      const source = String(edge.source);
      const target = String(edge.target);
      if (neighbors.has(source)) neighbors.get(source).add(target);
      if (neighbors.has(target)) neighbors.get(target).add(source);
    });

    const groups = graph.metadata.source_groups || {};
    document.getElementById("legend").innerHTML = Object.entries(groups)
      .map(([name, color]) => `<span><i class="swatch" style="background:${color}"></i>${escapeHtml(name)}</span>`)
      .join("");

    function applyTheme(theme) {
      if (theme === "dark") document.body.classList.add("theme-dark");
      if (theme === "light") document.body.classList.add("theme-light");
    }

    function edgeLine(edge) {
      const source = byId.get(String(edge.source));
      const target = byId.get(String(edge.target));
      if (!source || !target) return "";
      const width = Math.max(0.45, Math.min(2.3, Number(edge.score || 0) * 1.6 + 0.35));
      return `<line class="edge" data-source="${escapeAttr(edge.source)}" data-target="${escapeAttr(edge.target)}" x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}" stroke="${escapeAttr(edge.color || "#94a3b8")}" stroke-width="${width}"></line>`;
    }

    function nodeCircle(node) {
      const radius = Math.max(4.8, Math.min(19, 5.2 + Math.sqrt(Number(node.degree || 0)) * 2.05));
      const fill = node.is_center ? "#4f46e5" : nodeFill(node);
      const centerClass = node.is_center ? " center" : "";
      return `<circle class="node${centerClass}" data-id="${escapeAttr(node.id)}" cx="${node.x}" cy="${node.y}" r="${radius}" fill="${fill}"></circle>`;
    }

    function nodeFill(node) {
      if (Number(node.out_degree || 0) > Number(node.in_degree || 0)) return "#159895";
      if (Number(node.in_degree || 0) > 2) return "#2563eb";
      return "#64748b";
    }

    function nodeLabel(node) {
      const text = String(node.label || node.id).slice(0, 38);
      const hidden = node.label_visible ? "" : " hidden";
      return `<text class="label${hidden}" data-id="${escapeAttr(node.id)}" x="${Number(node.x) + 8}" y="${Number(node.y) - 8}">${escapeHtml(text)}</text>`;
    }

    function render() {
      viewport.innerHTML = `<g class="edges">${edges.map(edgeLine).join("")}</g><g class="nodes">${nodes.map(nodeCircle).join("")}</g><g class="labels">${nodes.map(nodeLabel).join("")}</g>`;
      bindNodeEvents();
      applyLabels();
      applySearch();
    }

    function bindNodeEvents() {
      viewport.querySelectorAll(".node").forEach(element => {
        element.addEventListener("mousemove", event => {
          const node = byId.get(String(element.dataset.id));
          if (!node) return;
          showTooltip(node, event);
          highlightNeighborhood(String(node.id));
        });
        element.addEventListener("mouseleave", () => {
          tooltip.style.display = "none";
          clearHighlights();
          applySearch();
        });
      });
    }

    function showTooltip(node, event) {
      tooltip.innerHTML = `<strong>${escapeHtml(node.label || node.id)}</strong><br>item_id: ${escapeHtml(node.item_id)}<br>degree: ${node.degree}<br>out: ${node.out_degree} / in: ${node.in_degree}`;
      tooltip.style.left = `${event.clientX + 14}px`;
      tooltip.style.top = `${event.clientY + 14}px`;
      tooltip.style.display = "block";
    }

    function highlightNeighborhood(nodeId) {
      const visible = neighbors.get(nodeId) || new Set([nodeId]);
      viewport.querySelectorAll(".node, .label").forEach(element => {
        const isVisible = visible.has(String(element.dataset.id));
        element.classList.toggle("dimmed", !isVisible);
        element.classList.toggle("highlight", String(element.dataset.id) === nodeId);
      });
      viewport.querySelectorAll(".edge").forEach(element => {
        const isVisible = visible.has(String(element.dataset.source)) && visible.has(String(element.dataset.target));
        element.classList.toggle("dimmed", !isVisible);
        element.classList.toggle("highlight", isVisible);
      });
    }

    function clearHighlights() {
      viewport.querySelectorAll(".node, .label, .edge").forEach(element => {
        element.classList.remove("dimmed", "highlight");
      });
    }

    function applySearch() {
      const query = search.value.trim().toLowerCase();
      const matches = new Set();
      if (query) {
        nodes.forEach(node => {
          const haystack = `${node.item_id} ${node.label || ""} ${node.item_name || ""}`.toLowerCase();
          if (haystack.includes(query)) matches.add(String(node.id));
        });
      }
      viewport.querySelectorAll(".node, .label, .edge").forEach(element => {
        element.classList.remove("dimmed", "highlight");
        if (!query) return;
        if (element.classList.contains("edge")) {
          const visible = matches.has(String(element.dataset.source)) || matches.has(String(element.dataset.target));
          element.classList.toggle("dimmed", !visible);
          element.classList.toggle("highlight", visible);
        } else {
          const isMatch = matches.has(String(element.dataset.id));
          element.classList.toggle("highlight", isMatch);
          element.classList.toggle("dimmed", !isMatch);
        }
      });
    }

    function applyLabels() {
      const mode = labelsMode.value;
      const important = new Set(
        [...nodes]
          .sort((left, right) => Number(right.is_center) - Number(left.is_center) || Number(right.degree || 0) - Number(left.degree || 0))
          .slice(0, graph.metadata.mode === "ego" ? 80 : 45)
          .map(node => String(node.id))
      );
      viewport.querySelectorAll(".label").forEach(element => {
        const node = byId.get(String(element.dataset.id));
        let visible = Boolean(node && node.label_visible);
        if (mode === "all") visible = true;
        if (mode === "off") visible = false;
        if (mode === "auto") visible = Boolean(node && (node.is_center || important.has(String(node.id))));
        if (mode === "important") visible = Boolean(node && (node.is_center || node.label_visible));
        element.classList.toggle("hidden", !visible);
      });
    }

    function updateTransform() {
      viewport.setAttribute("transform", `translate(${transform.x} ${transform.y}) scale(${transform.k})`);
    }

    function resetView() {
      transform = {x: 0, y: 0, k: 1};
      updateTransform();
    }

    function clientPoint(event) {
      const rect = svg.getBoundingClientRect();
      return {
        x: ((event.clientX - rect.left) / rect.width) * width,
        y: ((event.clientY - rect.top) / rect.height) * height,
      };
    }

    svg.addEventListener("wheel", event => {
      event.preventDefault();
      const point = clientPoint(event);
      const previousScale = transform.k;
      const nextScale = Math.max(0.22, Math.min(6, previousScale * (event.deltaY < 0 ? 1.12 : 0.88)));
      transform.x = point.x - ((point.x - transform.x) / previousScale) * nextScale;
      transform.y = point.y - ((point.y - transform.y) / previousScale) * nextScale;
      transform.k = nextScale;
      updateTransform();
    }, {passive: false});

    svg.addEventListener("pointerdown", event => {
      svg.setPointerCapture(event.pointerId);
      svg.classList.add("dragging");
      dragStart = {clientX: event.clientX, clientY: event.clientY, x: transform.x, y: transform.y};
    });
    svg.addEventListener("pointermove", event => {
      if (!dragStart) return;
      const rect = svg.getBoundingClientRect();
      transform.x = dragStart.x + ((event.clientX - dragStart.clientX) / rect.width) * width;
      transform.y = dragStart.y + ((event.clientY - dragStart.clientY) / rect.height) * height;
      updateTransform();
    });
    svg.addEventListener("pointerup", event => {
      svg.releasePointerCapture(event.pointerId);
      svg.classList.remove("dragging");
      dragStart = null;
    });
    svg.addEventListener("pointercancel", () => {
      svg.classList.remove("dragging");
      dragStart = null;
    });
    svg.addEventListener("dblclick", resetView);
    resetButton.addEventListener("click", resetView);
    search.addEventListener("input", applySearch);
    labelsMode.addEventListener("change", applyLabels);

    function escapeHtml(value) {
      return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
    }
    function escapeAttr(value) {
      return escapeHtml(value).replaceAll("'", "&#39;");
    }

    render();
    updateTransform();
  </script>
</body>
</html>
"""
