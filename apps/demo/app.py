"""Streamlit UI for browsing similar product recommendations."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import polars as pl
import streamlit as st
import streamlit.components.v1 as components

from apps.demo.demo_data import (
    METRIC_KEYS,
    build_item_catalog,
    choose_random_item,
    find_recommendation_path,
    load_json,
    load_recommendations,
    normalize_recommendations,
    recommendations_for_item,
    resolve_run_dir,
    search_items,
    source_distribution,
    source_explanation,
    source_group,
)

PAGE_TITLE = "Ozon Similar Products Demo"
DEFAULT_MANIFEST_PATH = Path("outputs/latest/manifest.json")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=PAGE_TITLE)
    parser.add_argument("--manifest-path", type=Path, default=None)
    parser.add_argument("--enriched-path", type=Path, default=None)
    parser.add_argument("--detailed-path", type=Path, default=None)
    parser.add_argument("--top-k", type=int, default=20)
    args, _unknown = parser.parse_known_args(argv)
    return args


def main() -> None:
    args = parse_args(sys.argv[1:])
    manifest_path = args.manifest_path
    if manifest_path is None and args.enriched_path is None and args.detailed_path is None:
        manifest_path = DEFAULT_MANIFEST_PATH
    st.set_page_config(
        page_title="Ozon Similar Products Demo",
        page_icon="🛒",
        layout="wide",
    )
    _inject_css()

    st.markdown(
        """
        <section class="demo-hero">
          <div>
            <p class="eyebrow">Offline recommender presentation</p>
            <h1>Ozon Similar Products Demo</h1>
            <p class="hero-copy">
              Search products, inspect similar items, and explain recommendation
              sources from the latest pipeline artifacts.
            </p>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )

    try:
        state = _load_state(
            manifest_path=manifest_path,
            enriched_path=args.enriched_path,
            detailed_path=args.detailed_path,
        )
    except (FileNotFoundError, TypeError, ValueError) as error:
        st.error(str(error))
        st.info(
            "Build recommendations first or pass an explicit parquet path with "
            "`-- --enriched-path ...`."
        )
        return

    tab_similar, tab_summary, tab_graph, tab_about = st.tabs(
        ["🔎 Similar items", "📊 Run summary", "🕸 Graph view", "ℹ️ About"]
    )
    with tab_similar:
        _render_similar_items_tab(state=state, top_k=args.top_k)
    with tab_summary:
        _render_run_summary_tab(state=state, top_k=args.top_k)
    with tab_graph:
        _render_graph_tab(state=state)
    with tab_about:
        _render_about_tab()


@st.cache_data(show_spinner=False)
def _load_state(
        *,
        manifest_path: Path | None,
        enriched_path: Path | None,
        detailed_path: Path | None,
) -> dict[str, Any]:
    recommendation_path = find_recommendation_path(
        manifest_path=manifest_path,
        enriched_path=enriched_path,
        detailed_path=detailed_path,
    )
    manifest: dict[str, Any] | None = None
    resolved_manifest_path: Path | None = None
    if manifest_path is not None and manifest_path.exists():
        resolved_manifest_path = manifest_path.resolve()
        manifest = load_json(resolved_manifest_path)

    frame = normalize_recommendations(load_recommendations(recommendation_path))
    catalog = build_item_catalog(frame)
    run_dir = (
        resolve_run_dir(resolved_manifest_path)
        if resolved_manifest_path is not None
        else recommendation_path.parent.parent
    )
    return {
        "manifest": manifest,
        "manifest_path": resolved_manifest_path,
        "recommendation_path": recommendation_path,
        "run_dir": run_dir,
        "frame": frame,
        "catalog": catalog,
    }


def _render_similar_items_tab(*, state: dict[str, Any], top_k: int) -> None:
    frame: pl.DataFrame = state["frame"]
    catalog: pl.DataFrame = state["catalog"]
    if "selected_item_id" not in st.session_state:
        st.session_state.selected_item_id = None

    left, right = st.columns([0.78, 0.22], vertical_alignment="bottom")
    with left:
        query = st.text_input(
            "Search item by item_id or product name",
            placeholder="Example: 123456 or milk",
        )
    with right:
        if st.button("🎲 Random item", use_container_width=True):
            selected = choose_random_item(catalog)
            st.session_state.selected_item_id = selected["item_id"] if selected else None

    matches = search_items(catalog, query)
    if not matches.is_empty():
        options = [_format_item_option(row) for row in matches.to_dicts()]
        selected_label = st.selectbox("Matching products", options=options, index=0)
        selected_item_id = selected_label.split(" · ", maxsplit=1)[0]
        st.session_state.selected_item_id = selected_item_id
    elif query.strip():
        st.warning("No matching products found. Try another item_id or product name.")
    elif st.session_state.selected_item_id is None:
        st.markdown(
            """
            <div class="empty-state">
              <h3>Pick a product to start</h3>
              <p>Use search or the random item button to inspect recommendations.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    selected_item_id = st.session_state.selected_item_id
    if selected_item_id is None:
        return

    selected_catalog_rows = catalog.filter(pl.col("item_id").cast(pl.Utf8) == str(selected_item_id))
    selected_row = (
        selected_catalog_rows.to_dicts()[0]
        if not selected_catalog_rows.is_empty()
        else {"item_id": selected_item_id, "item_name": None, "recommendation_count": 0}
    )
    recommendations = recommendations_for_item(frame, selected_item_id, top_k=top_k)

    if recommendations.is_empty():
        _render_selected_item_card(selected_row, recommendations)
        st.info(
            "No recommendations found for this item_id in the selected run. "
            "Try another item or use Random item."
        )
        return

    _render_selected_item_card(selected_row, recommendations)
    dist = source_distribution(recommendations)
    _render_source_badges(dist)
    st.dataframe(
        _display_recommendations(recommendations),
        use_container_width=True,
        hide_index=True,
    )


def _render_selected_item_card(item: dict[str, Any], recommendations: pl.DataFrame) -> None:
    distribution = source_distribution(recommendations)
    distribution_rows = {
        row["source"]: int(row["count"])
        for row in distribution.to_dicts()
    }
    behavioral_count = distribution_rows.get("behavioral", 0)
    fallback_count = sum(
        count for source, count in distribution_rows.items() if str(source).startswith("fallback_")
    )
    top_score = recommendations["score"].max() if not recommendations.is_empty() else None

    st.markdown(
        f"""
        <section class="selected-card">
          <div>
            <p class="eyebrow">Selected item</p>
            <h2>{_html_escape(str(item.get("item_id")))}</h2>
            <p class="item-name">{_html_escape(_display_name(item.get("item_name")))}</p>
          </div>
          <div class="metric-grid">
            <div class="mini-metric"><span>recommendations</span><strong>{recommendations.height}</strong></div>
            <div class="mini-metric"><span>behavioral</span><strong>{behavioral_count}</strong></div>
            <div class="mini-metric"><span>fallback</span><strong>{fallback_count}</strong></div>
            <div class="mini-metric"><span>top score</span><strong>{_format_score(top_score)}</strong></div>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_source_badges(distribution: pl.DataFrame) -> None:
    if distribution.is_empty():
        return
    badges = []
    for row in distribution.to_dicts():
        source = row["source"]
        count = row["count"]
        badges.append(
            f'<span class="source-badge {_source_class(source)}">'
            f"{_html_escape(_source_label(source))}: {count}"
            "</span>"
        )
    st.markdown(f'<div class="badge-row">{"".join(badges)}</div>', unsafe_allow_html=True)


def _display_recommendations(recommendations: pl.DataFrame) -> pl.DataFrame:
    return (
        recommendations.with_columns(
            pl.col("score").round(6).alias("score"),
            pl.col("source")
            .fill_null("")
            .map_elements(_source_label, return_dtype=pl.Utf8)
            .alias("source"),
            pl.col("source")
            .fill_null("")
            .map_elements(source_explanation, return_dtype=pl.Utf8)
            .alias("explanation"),
        )
        .select(
            "rank",
            "similar_item_id",
            "similar_item_name",
            "score",
            "source",
            "explanation",
        )
    )


def _render_run_summary_tab(*, state: dict[str, Any], top_k: int) -> None:
    manifest: dict[str, Any] | None = state["manifest"]
    manifest_path: Path | None = state["manifest_path"]
    recommendation_path: Path = state["recommendation_path"]
    run_dir: Path = state["run_dir"]

    summary = {
        "run_id": _manifest_value(manifest, "run_id", "unknown"),
        "manifest path": manifest_path.as_posix() if manifest_path else "not provided",
        "recommendation artifact": recommendation_path.as_posix(),
        "train window": _train_window(manifest),
        "top_k": _manifest_value(manifest, "top_k", top_k),
        "created_at": _manifest_value(manifest, "created_at", _manifest_value(manifest, "generated_at", "—")),
    }
    columns = st.columns(3)
    for index, (label, value) in enumerate(summary.items()):
        with columns[index % len(columns)]:
            st.metric(label, str(value))

    rows = manifest.get("rows") if isinstance(manifest, dict) else None
    if isinstance(rows, dict) and rows:
        st.subheader("Pipeline rows")
        st.dataframe(
            pl.DataFrame(
                {"stage": list(rows.keys()), "rows": [str(value) for value in rows.values()]}
            ),
            use_container_width=True,
            hide_index=True,
        )

    config_path = _config_path(manifest)
    if config_path is not None:
        st.markdown(f"**Config:** `{config_path}`")

    metrics = _load_metrics(run_dir)
    if metrics is None:
        st.info("Metrics file was not found for this run.")
        return

    st.subheader("Metrics")
    flattened = _flatten_metrics(metrics)
    metric_rows = [
        {"metric": key, "value": flattened.get(key, "—")}
        for key in METRIC_KEYS
        if key in flattened
    ]
    if metric_rows:
        st.dataframe(pl.DataFrame(metric_rows), use_container_width=True, hide_index=True)
    else:
        st.info("Metrics file exists, but expected demo metrics were not found.")


def _render_graph_tab(*, state: dict[str, Any]) -> None:
    run_dir: Path = state["run_dir"]
    graph_html = _find_graph_html(run_dir)
    if graph_html is not None:
        components.html(graph_html.read_text(encoding="utf-8"), height=800, scrolling=True)
        return

    run_id = _manifest_value(state["manifest"], "run_id", "<run_id>")
    st.markdown(
        f"""
        <section class="empty-state graph-placeholder">
          <h2>Graph visualization placeholder</h2>
          <p>
            Later we will export a Gephi/Sigma.js graph for this run and place it here:
          </p>
          <code>outputs/runs/{_html_escape(str(run_id))}/demo/gephi/index.html</code>
          <p class="muted">Expected graph artifacts:</p>
          <ul>
            <li>graph.gexf for Gephi</li>
            <li>graph.html or gephi/index.html for browser embedding</li>
            <li>graph.json if needed</li>
          </ul>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_about_tab() -> None:
    st.markdown(
        """
        ### What this demo shows

        This demo visualizes item-to-item recommendations produced by the offline
        Ozon Similar Products pipeline.

        Behavioral recommendations are based on user co-visitation inside sessions.
        Fallback recommendations are added when behavioral candidates are not enough.
        Scores are produced by the graph/scoring pipeline and ranked into top-K
        similar products.

        ### Source labels

        - 🧠 behavioral: session co-visitation signal
        - 🧩 category/type fallback: popular item from the same category/type
        - 🧩 category fallback: popular item from the same category
        - 🧩 type fallback: popular item from the same type
        - 🏷 brand fallback: popular item from the same brand
        - 🔥 popular fallback: global popular item
        """
    )


def _load_metrics(run_dir: Path) -> dict[str, Any] | None:
    for path in (
        run_dir / "evaluation" / "metrics.json",
        run_dir / "metrics.json",
        run_dir / "evaluation" / "scorecard.json",
    ):
        if path.exists():
            return load_json(path)
    return None


def _find_graph_html(run_dir: Path) -> Path | None:
    for path in (
        run_dir / "demo" / "gephi" / "index.html",
        run_dir / "demo" / "graph.html",
        Path("apps/demo/assets/graph/index.html"),
    ):
        if path.exists():
            return path
    return None


def _display_name(value: Any) -> str:
    return "None" if value is None else str(value)


def _format_item_option(row: dict[str, Any]) -> str:
    return f"{row['item_id']} · {_display_name(row.get('item_name'))}"


def _format_score(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.4f}"


def _source_label(source: str | None) -> str:
    labels = {
        "behavioral": "🧠 behavioral",
        "fallback_category_type_popular": "🧩 category/type fallback",
        "fallback_category_popular": "🧩 category fallback",
        "fallback_type_popular": "🧩 type fallback",
        "fallback_brand_popular": "🏷 brand fallback",
        "fallback_global_popular": "🔥 popular fallback",
    }
    return labels.get(source or "", "❔ unknown source")


def _source_class(source: str | None) -> str:
    group = source_group(source)
    if "behavioral" in group:
        return "behavioral"
    if "brand" in group:
        return "brand"
    if "popular" in group:
        return "popular"
    if "fallback" in group:
        return "fallback"
    return "unknown"


def _manifest_value(manifest: dict[str, Any] | None, key: str, default: Any) -> Any:
    if not isinstance(manifest, dict):
        return default
    return manifest.get(key, default)


def _train_window(manifest: dict[str, Any] | None) -> str:
    if not isinstance(manifest, dict):
        return "—"
    start = manifest.get("window_start")
    end = manifest.get("window_end")
    if start and end:
        return f"{start} .. {end}"
    train_until = manifest.get("train_until_date")
    return str(train_until) if train_until else "—"


def _config_path(manifest: dict[str, Any] | None) -> str | None:
    if not isinstance(manifest, dict):
        return None
    for key in ("config_path", "config_snapshot_path"):
        value = manifest.get(key)
        if isinstance(value, str):
            return value
    paths = manifest.get("paths")
    if isinstance(paths, dict):
        for key in ("config_path", "config_snapshot_path"):
            value = paths.get(key)
            if isinstance(value, str):
                return value
    return None


def _flatten_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}

    def visit(value: Any) -> None:
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            if key in METRIC_KEYS and not isinstance(item, dict | list):
                flattened[key] = item
            elif isinstance(item, dict):
                visit(item)

    visit(payload)
    return flattened


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        :root {
          --demo-ink: #17202a;
          --demo-muted: #667085;
          --demo-line: #e6e8ef;
          --demo-panel: #ffffff;
          --demo-soft: #f6f8fb;
          --demo-accent: #1f7a8c;
          --demo-warm: #b76e3a;
        }
        .block-container {
          padding-top: 2rem;
          padding-bottom: 3rem;
        }
        .demo-hero {
          background: #f8fafc;
          border: 1px solid var(--demo-line);
          border-radius: 8px;
          padding: 26px 30px;
          margin-bottom: 18px;
        }
        .demo-hero h1 {
          color: var(--demo-ink);
          font-size: 2.45rem;
          line-height: 1.1;
          margin: 0.15rem 0 0.55rem;
          letter-spacing: 0;
        }
        .hero-copy,
        .muted,
        .empty-state p {
          color: var(--demo-muted);
        }
        .eyebrow {
          color: var(--demo-accent);
          font-size: 0.78rem;
          font-weight: 700;
          letter-spacing: 0;
          margin: 0;
          text-transform: uppercase;
        }
        .selected-card {
          display: grid;
          gap: 20px;
          grid-template-columns: minmax(240px, 1.2fr) minmax(360px, 2fr);
          align-items: stretch;
          background: var(--demo-panel);
          border: 1px solid var(--demo-line);
          border-radius: 8px;
          padding: 22px;
          margin: 18px 0;
          box-shadow: 0 12px 32px rgba(16, 24, 40, 0.06);
        }
        .selected-card h2 {
          color: var(--demo-ink);
          font-size: 1.85rem;
          margin: 0.2rem 0;
          letter-spacing: 0;
        }
        .item-name {
          color: var(--demo-muted);
          font-size: 1rem;
          margin: 0;
        }
        .metric-grid {
          display: grid;
          gap: 10px;
          grid-template-columns: repeat(4, minmax(120px, 1fr));
        }
        .mini-metric {
          background: var(--demo-soft);
          border: 1px solid var(--demo-line);
          border-radius: 8px;
          padding: 14px;
        }
        .mini-metric span {
          color: var(--demo-muted);
          display: block;
          font-size: 0.78rem;
          margin-bottom: 6px;
        }
        .mini-metric strong {
          color: var(--demo-ink);
          font-size: 1.35rem;
        }
        .badge-row {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin: 6px 0 16px;
        }
        .source-badge {
          border-radius: 999px;
          display: inline-flex;
          font-size: 0.84rem;
          font-weight: 700;
          padding: 7px 10px;
        }
        .source-badge.behavioral {
          background: #eaf6f8;
          color: #155e6e;
        }
        .source-badge.fallback {
          background: #f1f5f9;
          color: #475569;
        }
        .source-badge.brand {
          background: #fff4e6;
          color: #9a5a20;
        }
        .source-badge.popular {
          background: #fff1f2;
          color: #9f1239;
        }
        .source-badge.unknown {
          background: #f4f4f5;
          color: #52525b;
        }
        .empty-state {
          background: var(--demo-soft);
          border: 1px dashed #ccd3df;
          border-radius: 8px;
          padding: 26px;
          margin-top: 18px;
        }
        .empty-state h2,
        .empty-state h3 {
          color: var(--demo-ink);
          margin-top: 0;
          letter-spacing: 0;
        }
        .graph-placeholder code {
          background: #eef2f7;
          border: 1px solid #dde3ec;
          border-radius: 6px;
          color: #334155;
          display: inline-block;
          margin: 8px 0 12px;
          padding: 8px 10px;
        }
        @media (max-width: 900px) {
          .selected-card,
          .metric-grid {
            grid-template-columns: 1fr;
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
