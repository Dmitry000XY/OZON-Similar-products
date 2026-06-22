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
    max_edges: int = 2000
    max_nodes: int = 500
    ego_top_k: int = 20
    second_hop_top_k: int = 3
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
    if len(edge_rows) > config.max_edges:
        edge_rows = edge_rows[: config.max_edges]

    edge_rows = _drop_edges_for_node_limit(edge_rows, config)
    nodes = _build_nodes(edge_rows, config)
    edges = [_edge_payload(index, row) for index, row in enumerate(edge_rows)]

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
    if config.max_edges <= 0:
        raise ValueError("max_edges must be positive")
    if config.max_nodes <= 0:
        raise ValueError("max_nodes must be positive")
    if config.max_rank <= 0:
        raise ValueError("max_rank must be positive")
    if config.ego_top_k <= 0:
        raise ValueError("ego_top_k must be positive")
    if config.second_hop_top_k < 0:
        raise ValueError("second_hop_top_k must be non-negative")


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
    return recommendations.head(config.max_edges)


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

    return (
        _sort_for_graph(pl.concat(non_empty, how="vertical"))
        .unique(
            subset=["item_id", "similar_item_id"],
            keep="first",
        )
        .head(config.max_edges)
    )


def _drop_edges_for_node_limit(
    edge_rows: list[dict[str, Any]],
    config: RecommendationGraphConfig,
) -> list[dict[str, Any]]:
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
        ):
            ET.SubElement(
                values,
                f"{{{ns}}}attvalue",
                {"for": attr_id, "value": str(_json_value(node.get(attr_id)) or "")},
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
        ):
            ET.SubElement(
                values,
                f"{{{ns}}}attvalue",
                {"for": attr_id, "value": str(_json_value(edge.get(key)) or "")},
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


def _float_or_zero(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        parsed = float(value)
    except TypeError, ValueError:
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Recommendation graph</title>
  <style>
    :root {
      --ink: #17202a;
      --muted: #667085;
      --line: #d8dee9;
      --panel: #ffffff;
      --soft: #f6f8fb;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--soft);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .toolbar {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 14px 16px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      z-index: 2;
    }
    .toolbar input {
      width: min(360px, 60vw);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 14px;
    }
    .counts { color: var(--muted); font-size: 13px; margin-left: auto; }
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 10px 16px;
      background: #fbfcfe;
      border-bottom: 1px solid var(--line);
    }
    .legend span {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
    }
    .swatch { width: 12px; height: 12px; border-radius: 50%; display: inline-block; }
    #canvas {
      width: 100%;
      height: 720px;
      background: radial-gradient(circle at center, #ffffff 0, #f8fafc 68%, #eef2f7 100%);
      display: block;
    }
    .edge { stroke-opacity: 0.36; }
    .node { stroke: #fff; stroke-width: 1.5; cursor: pointer; }
    .node.dimmed, .edge.dimmed, .label.dimmed { opacity: 0.12; }
    .node.highlight { stroke: #111827; stroke-width: 3; }
    .label {
      fill: #334155;
      font-size: 10px;
      paint-order: stroke;
      stroke: white;
      stroke-width: 3px;
      stroke-linejoin: round;
      pointer-events: none;
    }
    #tooltip {
      position: fixed;
      pointer-events: none;
      background: rgba(15, 23, 42, 0.94);
      color: white;
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 12px;
      line-height: 1.35;
      max-width: 280px;
      display: none;
      z-index: 5;
    }
  </style>
</head>
<body>
  <div class="toolbar">
    <strong>Recommendation graph</strong>
    <input id="search" type="search" placeholder="Search item_id or name">
    <span class="counts" id="counts"></span>
  </div>
  <div class="legend" id="legend"></div>
  <svg id="canvas" viewBox="0 0 1200 720" role="img" aria-label="Recommendation graph"></svg>
  <div id="tooltip"></div>
  <script type="application/json" id="graph-data">__GRAPH_DATA__</script>
  <script>
    const graph = JSON.parse(document.getElementById("graph-data").textContent);
    const svg = document.getElementById("canvas");
    const tooltip = document.getElementById("tooltip");
    const counts = document.getElementById("counts");
    const search = document.getElementById("search");
    const width = 1200;
    const height = 720;
    const cx = width / 2;
    const cy = height / 2;
    const nodes = graph.nodes.map((node, index) => ({...node, index}));
    const edges = graph.edges;
    const byId = new Map(nodes.map(node => [String(node.id), node]));
    const maxDegree = Math.max(1, ...nodes.map(node => Number(node.degree || 0)));

    nodes.forEach((node, index) => {
      const degreeRank = Number(node.degree || 0) / maxDegree;
      if (node.is_center) {
        node.x = cx;
        node.y = cy;
      } else {
        const angle = (index / Math.max(1, nodes.length)) * Math.PI * 2;
        const radius = 80 + (1 - degreeRank) * 250 + (index % 7) * 16;
        node.x = cx + Math.cos(angle) * radius;
        node.y = cy + Math.sin(angle) * radius;
      }
    });

    counts.textContent = `${nodes.length} nodes · ${edges.length} edges`;
    const groups = graph.metadata.source_groups || {};
    document.getElementById("legend").innerHTML = Object.entries(groups)
      .map(([name, color]) => `<span><i class="swatch" style="background:${color}"></i>${name}</span>`)
      .join("");

    function line(edge) {
      const source = byId.get(String(edge.source));
      const target = byId.get(String(edge.target));
      if (!source || !target) return "";
      return `<line class="edge" data-source="${edge.source}" data-target="${edge.target}" x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}" stroke="${edge.color || "#94a3b8"}" stroke-width="${Math.max(0.8, Math.min(5, Number(edge.score || 0) * 4 + 0.8))}"></line>`;
    }

    function circle(node) {
      const radius = Math.max(5, Math.min(18, 5 + Math.sqrt(Number(node.degree || 0)) * 2.2));
      const fill = node.is_center ? "#4f46e5" : "#0f766e";
      return `<circle class="node" data-id="${node.id}" cx="${node.x}" cy="${node.y}" r="${radius}" fill="${fill}"></circle>`;
    }

    function label(node) {
      if (node.index > 40 && !node.is_center && Number(node.degree || 0) < 3) return "";
      const text = String(node.label || node.id).slice(0, 34);
      return `<text class="label" data-id="${node.id}" x="${node.x + 8}" y="${node.y - 8}">${escapeHtml(text)}</text>`;
    }

    function escapeHtml(value) {
      return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
    }

    svg.innerHTML = `<g>${edges.map(line).join("")}</g><g>${nodes.map(circle).join("")}</g><g>${nodes.map(label).join("")}</g>`;

    svg.querySelectorAll(".node").forEach(element => {
      element.addEventListener("mousemove", event => {
        const node = byId.get(String(element.dataset.id));
        tooltip.innerHTML = `<strong>${escapeHtml(node.label || node.id)}</strong><br>item_id: ${escapeHtml(node.item_id)}<br>degree: ${node.degree}<br>out: ${node.out_degree} · in: ${node.in_degree}`;
        tooltip.style.left = `${event.clientX + 14}px`;
        tooltip.style.top = `${event.clientY + 14}px`;
        tooltip.style.display = "block";
      });
      element.addEventListener("mouseleave", () => { tooltip.style.display = "none"; });
    });

    search.addEventListener("input", () => {
      const query = search.value.trim().toLowerCase();
      const matches = new Set();
      if (query) {
        nodes.forEach(node => {
          const haystack = `${node.item_id} ${node.label || ""} ${node.item_name || ""}`.toLowerCase();
          if (haystack.includes(query)) matches.add(String(node.id));
        });
      }
      svg.querySelectorAll(".node, .label, .edge").forEach(element => {
        element.classList.remove("dimmed", "highlight");
        if (!query) return;
        if (element.classList.contains("edge")) {
          const visible = matches.has(String(element.dataset.source)) || matches.has(String(element.dataset.target));
          if (!visible) element.classList.add("dimmed");
        } else {
          const isMatch = matches.has(String(element.dataset.id));
          if (isMatch) element.classList.add("highlight"); else element.classList.add("dimmed");
        }
      });
    });
  </script>
</body>
</html>
"""
