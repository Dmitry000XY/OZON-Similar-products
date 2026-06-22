"""Tests for recommendation graph artifact generation."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from ozon_similar_products.visualization import (
    RecommendationGraphConfig,
    build_recommendation_graph,
    export_recommendation_graph,
)


def test_build_recommendation_graph_creates_nodes_and_edges() -> None:
    graph = build_recommendation_graph(_recommendations(), RecommendationGraphConfig())

    assert graph.metadata["mode"] == "overview"
    assert len(graph.nodes) >= 4
    assert graph.edges[0]["source"] == "1"
    assert graph.edges[0]["target"] == "2"
    assert graph.edges[0]["recommendation_source"] == "behavioral"
    assert all("x" in node and "y" in node for node in graph.nodes)
    assert any(node["label_visible"] for node in graph.nodes)


def test_build_recommendation_graph_filters_max_rank() -> None:
    graph = build_recommendation_graph(_recommendations(), RecommendationGraphConfig(max_rank=1))

    assert {edge["rank"] for edge in graph.edges} == {1}


def test_build_recommendation_graph_can_exclude_fallback() -> None:
    graph = build_recommendation_graph(
        _recommendations(),
        RecommendationGraphConfig(include_fallback=False),
    )

    assert {edge["recommendation_source"] for edge in graph.edges} == {"behavioral"}


def test_build_recommendation_graph_limits_edges() -> None:
    graph = build_recommendation_graph(_recommendations(), RecommendationGraphConfig(max_edges=2))

    assert len(graph.edges) == 2


def test_build_recommendation_graph_accepts_unlimited_edges_and_nodes() -> None:
    graph = build_recommendation_graph(
        _recommendations(),
        RecommendationGraphConfig(max_edges=None, max_nodes=None),
    )

    assert len(graph.edges) == 5
    assert graph.metadata["max_edges"] is None
    assert graph.metadata["max_nodes"] is None


def test_build_recommendation_graph_builds_ego_graph_with_second_hop() -> None:
    graph = build_recommendation_graph(
        _recommendations(),
        RecommendationGraphConfig(
            mode="ego",
            selected_item_id=1,
            ego_top_k=2,
            second_hop_top_k=1,
            max_edges=100,
        ),
    )

    edge_pairs = {(edge["source"], edge["target"]) for edge in graph.edges}
    assert ("1", "2") in edge_pairs
    assert ("1", "3") in edge_pairs
    assert ("2", "4") in edge_pairs
    assert any(node["id"] == "1" and node["is_center"] for node in graph.nodes)
    center = next(node for node in graph.nodes if node["id"] == "1")
    assert center["x"] == 600.0
    assert center["y"] == 430.0
    assert center["label_visible"] is True


def test_build_recommendation_graph_respects_labels_mode() -> None:
    without_labels = build_recommendation_graph(
        _recommendations(),
        RecommendationGraphConfig(labels_mode="off"),
    )
    all_labels = build_recommendation_graph(
        _recommendations(),
        RecommendationGraphConfig(labels_mode="all"),
    )

    assert {node["label_visible"] for node in without_labels.nodes} == {False}
    assert {node["label_visible"] for node in all_labels.nodes} == {True}


def test_build_recommendation_graph_keeps_stable_source_colors() -> None:
    graph = build_recommendation_graph(_recommendations(), RecommendationGraphConfig())

    assert graph.metadata["source_groups"]["behavioral"] == "#159895"
    assert graph.metadata["source_groups"]["category_fallback"] == "#f59e0b"


def test_export_recommendation_graph_writes_artifacts(tmp_path: Path) -> None:
    recommendation_path = tmp_path / "recommendations.parquet"
    _recommendations().write_parquet(recommendation_path)

    result = export_recommendation_graph(
        recommendation_path=recommendation_path,
        output_dir=tmp_path / "graph",
        config=RecommendationGraphConfig(),
    )

    assert result.html_path is not None and result.html_path.exists()
    assert result.json_path is not None and result.json_path.exists()
    assert result.gexf_path is not None and result.gexf_path.exists()
    assert result.manifest_path.exists()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifact_type"] == "recommendation_graph"
    assert manifest["paths"]["html"] == "recommendations_graph.html"
    html = result.html_path.read_text(encoding="utf-8")
    assert "resetView" in html
    assert "wheel" in html
    assert "pointerdown" in html
    assert "labels-mode" in html
    gexf = result.gexf_path.read_text(encoding="utf-8")
    assert 'title="is_center"' in gexf
    assert 'title="label_visible"' in gexf
    assert 'title="x"' in gexf
    assert 'title="color"' in gexf
    assert 'for="is_center" value="false"' in gexf


def test_build_recommendation_graph_handles_empty_input() -> None:
    graph = build_recommendation_graph(
        pl.DataFrame(schema={"item_id": pl.Int64, "similar_item_id": pl.Int64}),
        RecommendationGraphConfig(),
    )

    assert graph.nodes == []
    assert graph.edges == []


def test_build_recommendation_graph_uses_item_id_label_without_names() -> None:
    graph = build_recommendation_graph(
        pl.DataFrame(
            {
                "item_id": [10],
                "similar_item_id": [20],
                "rank": [1],
                "score": [0.9],
                "source": ["behavioral"],
            }
        ),
        RecommendationGraphConfig(),
    )

    labels = {node["id"]: node["label"] for node in graph.nodes}
    assert labels["10"] == "10"
    assert labels["20"] == "20"


def _recommendations() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "item_id": [1, 1, 1, 2, 3, 4],
            "item_name": ["Milk", "Milk", "Milk", "Tea", "Coffee", "Bread"],
            "similar_item_id": [2, 3, 5, 4, 4, 1],
            "similar_item_name": ["Tea", "Coffee", "Sugar", "Bread", "Bread", "Milk"],
            "rank": [1, 2, 11, 1, 1, 1],
            "score": [0.95, 0.8, 0.2, 0.7, 0.6, 0.5],
            "source": [
                "behavioral",
                "fallback_category_popular",
                "fallback_global_popular",
                "behavioral",
                "fallback_brand_popular",
                "behavioral",
            ],
        }
    )
