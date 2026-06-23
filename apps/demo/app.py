"""Streamlit UI for browsing similar product recommendations."""

from __future__ import annotations

import argparse
import html
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import polars as pl
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.demo.demo_data import (  # noqa: E402
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
)
from apps.demo.texts import (  # noqa: E402
    LANGUAGES,
    get_texts,
    recommendation_column_names,
    source_explanation,
    source_label,
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

    st.set_page_config(page_title=PAGE_TITLE, page_icon="🛒", layout="wide")
    _inject_css()

    language = _language_selector()
    texts = get_texts(language)
    _render_hero(texts)

    try:
        state = _load_state(
            manifest_path=manifest_path,
            enriched_path=args.enriched_path,
            detailed_path=args.detailed_path,
        )
    except (FileNotFoundError, TypeError, ValueError) as error:
        st.error(str(error))
        st.info(texts["errors"]["load_failed_hint"])
        return

    tabs = texts["tabs"]
    tab_similar, tab_summary, tab_about = st.tabs(
        [tabs["similar"], tabs["summary"], tabs["about"]]
    )
    with tab_similar:
        _render_similar_items_tab(state=state, top_k=args.top_k, texts=texts, language=language)
    with tab_summary:
        _render_run_summary_tab(state=state, top_k=args.top_k, texts=texts)
    with tab_about:
        _render_about_tab(texts)


def _language_selector() -> str:
    if "demo_language" not in st.session_state:
        st.session_state.demo_language = "EN"
    st.markdown('<div class="language-offset"></div>', unsafe_allow_html=True)
    _, right = st.columns([0.84, 0.16], vertical_alignment="bottom")
    with right:
        language = st.radio(
            "Language / Язык",
            list(LANGUAGES),
            horizontal=True,
            key="demo_language",
            label_visibility="visible",
        )
    return str(language)


def _render_hero(texts: Mapping[str, Any]) -> None:
    st.markdown(
        f"""
        <section class="demo-hero">
          <p class="eyebrow">{_escape(texts["hero_eyebrow"])}</p>
          <h1>{_escape(texts["hero_title"])}</h1>
          <p class="hero-copy">{_escape(texts["hero_copy"])}</p>
        </section>
        """,
        unsafe_allow_html=True,
    )


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


def _render_similar_items_tab(
    *,
    state: dict[str, Any],
    top_k: int,
    texts: Mapping[str, Any],
    language: str,
) -> None:
    similar_texts = texts["similar"]
    frame: pl.DataFrame = state["frame"]
    catalog: pl.DataFrame = state["catalog"]

    if "selected_item_id" not in st.session_state:
        st.session_state.selected_item_id = None

    left, right = st.columns([0.78, 0.22], vertical_alignment="bottom")
    with left:
        query = st.text_input(
            similar_texts["search_label"],
            placeholder=similar_texts["search_placeholder"],
        )
    with right:
        if st.button(similar_texts["random_button"], use_container_width=True):
            selected = choose_random_item(catalog)
            st.session_state.selected_item_id = selected["item_id"] if selected else None

    matches = search_items(catalog, query)
    if not matches.is_empty():
        options = [_format_item_option(row) for row in matches.to_dicts()]
        selected_label = st.selectbox(similar_texts["matches"], options=options, index=0)
        selected_item_id = selected_label.split(" · ", maxsplit=1)[0]
        st.session_state.selected_item_id = selected_item_id
    elif query.strip():
        st.warning(similar_texts["no_matches"])
    elif st.session_state.selected_item_id is None:
        st.markdown(
            f"""
            <div class="empty-state">
              <h3>{_escape(similar_texts["empty_title"])}</h3>
              <p>{_escape(similar_texts["empty_body"])}</p>
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
    _render_selected_item_card(selected_row, recommendations, texts)

    if recommendations.is_empty():
        st.info(similar_texts["no_recommendations"])
        return

    _render_source_badges(source_distribution(recommendations), language=language)
    st.dataframe(
        _display_recommendations(recommendations, language=language),
        use_container_width=True,
        hide_index=True,
        height=720,
    )


def _render_selected_item_card(
    item: dict[str, Any],
    recommendations: pl.DataFrame,
    texts: Mapping[str, Any],
) -> None:
    similar_texts = texts["similar"]
    distribution = source_distribution(recommendations)
    distribution_rows = {row["source"]: int(row["count"]) for row in distribution.to_dicts()}
    behavioral_count = distribution_rows.get("behavioral", 0)
    fallback_count = sum(
        count for source, count in distribution_rows.items() if str(source).startswith("fallback_")
    )
    top_score = recommendations["score"].max() if not recommendations.is_empty() else None
    item_name = _display_name(item.get("item_name"))
    item_id = _display_name(item.get("item_id"))

    st.markdown(
        f"""
        <section class="selected-card">
          <div class="selected-main">
            <p class="eyebrow">{_escape(similar_texts["selected"])}</p>
            <h2 title="{_escape(item_name)}">{_escape(item_name)}</h2>
            <p class="item-id">{_escape(similar_texts["item_id"])}: <strong>{_escape(item_id)}</strong></p>
          </div>
          <div class="metric-grid">
            <div class="mini-metric"><span>{_escape(similar_texts["recommendations"])}</span><strong>{recommendations.height}</strong></div>
            <div class="mini-metric"><span>{_escape(similar_texts["behavioral"])}</span><strong>{behavioral_count}</strong></div>
            <div class="mini-metric"><span>{_escape(similar_texts["fallback"])}</span><strong>{fallback_count}</strong></div>
            <div class="mini-metric"><span>{_escape(similar_texts["top_score"])}</span><strong>{_format_score(top_score)}</strong></div>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_source_badges(distribution: pl.DataFrame, *, language: str) -> None:
    if distribution.is_empty():
        return
    badges = []
    for row in distribution.to_dicts():
        source = row["source"]
        count = row["count"]
        badges.append(
            f'<span class="source-badge {_source_class(source)}">'
            f"{_escape(source_label(source, language))}: {count}"
            "</span>"
        )
    st.markdown(f'<div class="badge-row">{"".join(badges)}</div>', unsafe_allow_html=True)


def _display_recommendations(recommendations: pl.DataFrame, *, language: str) -> pl.DataFrame:
    display = (
        recommendations.with_columns(
            pl.col("score").round(6).alias("score"),
            pl.col("source")
            .fill_null("")
            .map_elements(lambda value: source_label(value, language), return_dtype=pl.Utf8)
            .alias("source"),
            pl.col("source")
            .fill_null("")
            .map_elements(lambda value: source_explanation(value, language), return_dtype=pl.Utf8)
            .alias("explanation"),
        )
        .select("rank", "similar_item_id", "similar_item_name", "score", "source", "explanation")
    )
    return display.rename(recommendation_column_names(language))


def _render_run_summary_tab(*, state: dict[str, Any], top_k: int, texts: Mapping[str, Any]) -> None:
    summary_texts = texts["summary"]
    manifest: dict[str, Any] | None = state["manifest"]
    manifest_path: Path | None = state["manifest_path"]
    recommendation_path: Path = state["recommendation_path"]
    run_dir: Path = state["run_dir"]

    summary = {
        summary_texts["cards"]["run_id"]: _manifest_value(manifest, "run_id", summary_texts["unknown"]),
        summary_texts["cards"]["manifest_path"]: (
            manifest_path.as_posix() if manifest_path else summary_texts["not_provided"]
        ),
        summary_texts["cards"]["recommendation_artifact"]: recommendation_path.as_posix(),
        summary_texts["cards"]["train_window"]: _train_window(manifest),
        summary_texts["cards"]["top_k"]: _manifest_value(manifest, "top_k", top_k),
        summary_texts["cards"]["created_at"]: _manifest_value(
            manifest,
            "created_at",
            _manifest_value(manifest, "generated_at", "—"),
        ),
    }
    _render_summary_cards(summary)

    rows = manifest.get("rows") if isinstance(manifest, dict) else None
    if isinstance(rows, dict) and rows:
        st.subheader(summary_texts["pipeline_rows"])
        row_data = [
            {summary_texts["stage"]: key, summary_texts["rows"]: _format_int_like(value)}
            for key, value in rows.items()
        ]
        _render_html_table(row_data, [summary_texts["stage"], summary_texts["rows"]])

    config_path = _config_path(manifest)
    if config_path is not None:
        st.markdown(f"**{_escape(summary_texts['config'])}:** `{config_path}`")

    metrics = _load_metrics(run_dir)
    if metrics is None:
        st.info(summary_texts["metrics_missing"])
        return

    st.subheader(summary_texts["metrics"])
    flattened = _flatten_metrics(metrics)
    metric_rows = [
        {
            summary_texts["metric"]: key,
            summary_texts["value"]: _format_metric_value(flattened.get(key, "—")),
        }
        for key in METRIC_KEYS
        if key in flattened
    ]
    if metric_rows:
        _render_html_table(metric_rows, [summary_texts["metric"], summary_texts["value"]])
    else:
        st.info(summary_texts["metrics_unexpected"])


def _render_summary_cards(summary: Mapping[str, Any]) -> None:
    cards = []
    for label, value in summary.items():
        cards.append(
            f"""
            <div class="summary-card">
              <div class="summary-label">{_escape(str(label))}</div>
              <code class="summary-value">{_escape(str(value))}</code>
            </div>
            """
        )
    st.markdown(f'<section class="summary-grid">{"".join(cards)}</section>', unsafe_allow_html=True)


def _render_html_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    header = "".join(f"<th>{_escape(column)}</th>" for column in columns)
    body = []
    for row in rows:
        body.append(
            "<tr>"
            + "".join(f"<td>{_escape(str(row.get(column, '—')))}</td>" for column in columns)
            + "</tr>"
        )
    st.markdown(
        f"""
        <div class="table-wrap">
          <table class="demo-table">
            <colgroup>
              <col style="width:50%">
              <col style="width:50%">
            </colgroup>
            <thead><tr>{header}</tr></thead>
            <tbody>{''.join(body)}</tbody>
          </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_about_tab(texts: Mapping[str, Any]) -> None:
    about = texts["about"]
    source_items = "\n".join(f"- {item}" for item in about["sources"])
    st.markdown(
        f"""
### {about["title"]}

{about["body"]}

### {about["source_title"]}

{source_items}
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


def _display_name(value: Any) -> str:
    return "None" if value is None else str(value)


def _format_item_option(row: dict[str, Any]) -> str:
    return f"{row['item_id']} · {_display_name(row.get('item_name'))}"


def _format_score(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.4f}"


def _source_class(source: str | None) -> str:
    source_value = str(source or "")
    if source_value == "behavioral":
        return "behavioral"
    if "brand" in source_value:
        return "brand"
    if "global" in source_value or "popular" in source_value:
        return "popular"
    if source_value.startswith("fallback"):
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


def _format_metric_value(value: Any) -> str:
    if value is None or value == "—":
        return "—"
    if isinstance(value, bool):
        return str(value)
    try:
        return f"{float(value):.6f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _format_int_like(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        :root {
          --demo-card: var(--secondary-background-color, #f8fafc);
          --demo-panel: var(--secondary-background-color, #f8fafc);
          --demo-ink: var(--text-color, #17202a);
          --demo-muted: color-mix(in srgb, var(--text-color, #17202a) 62%, transparent);
          --demo-line: color-mix(in srgb, var(--text-color, #17202a) 16%, transparent);
          --demo-accent: var(--primary-color, #1f7a8c);
          --demo-shadow: rgba(16, 24, 40, 0.08);
        }
        .language-offset { height: 0.85rem; }
        .block-container {
          padding-top: 1.4rem;
          padding-bottom: 3rem;
        }
        .demo-hero,
        .selected-card,
        .summary-card,
        .empty-state,
        .mini-metric,
        .table-wrap {
          background: var(--demo-card);
          color: var(--demo-ink);
          border: 1px solid var(--demo-line);
        }
        .demo-hero {
          border-radius: 12px;
          padding: 26px 30px;
          margin: 12px 0 18px;
        }
        .demo-hero h1 {
          color: var(--demo-ink);
          font-size: 2.35rem;
          line-height: 1.1;
          margin: 0.15rem 0 0.55rem;
        }
        .hero-copy,
        .muted,
        .empty-state p,
        .item-id,
        .summary-label {
          color: var(--demo-muted);
        }
        .eyebrow {
          color: var(--demo-accent);
          font-size: 0.78rem;
          font-weight: 800;
          letter-spacing: 0.035em;
          margin: 0;
          text-transform: uppercase;
        }
        .selected-card {
          display: grid;
          gap: 20px;
          grid-template-columns: minmax(360px, 1.35fr) minmax(360px, 1.65fr);
          align-items: stretch;
          border-radius: 12px;
          padding: 22px;
          margin: 18px 0;
          box-shadow: 0 12px 32px var(--demo-shadow);
        }
        .selected-main h2 {
          color: var(--demo-ink);
          font-size: clamp(1.35rem, 1.8vw, 2.1rem);
          line-height: 1.18;
          margin: 0.35rem 0 0.55rem;
          overflow-wrap: anywhere;
          word-break: break-word;
          max-height: 5.2em;
          overflow-y: auto;
          padding-right: 4px;
        }
        .item-id {
          font-size: 0.96rem;
          margin: 0;
        }
        .metric-grid {
          display: grid;
          gap: 10px;
          grid-template-columns: repeat(4, minmax(120px, 1fr));
        }
        .mini-metric {
          border-radius: 10px;
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
          font-size: 1.25rem;
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
          font-weight: 800;
          padding: 7px 10px;
        }
        .source-badge.behavioral {
          background: color-mix(in srgb, #14b8a6 20%, transparent);
          color: color-mix(in srgb, #14b8a6 70%, var(--demo-ink));
        }
        .source-badge.fallback {
          background: color-mix(in srgb, #94a3b8 22%, transparent);
          color: color-mix(in srgb, #94a3b8 70%, var(--demo-ink));
        }
        .source-badge.brand {
          background: color-mix(in srgb, #f59e0b 22%, transparent);
          color: color-mix(in srgb, #f59e0b 72%, var(--demo-ink));
        }
        .source-badge.popular {
          background: color-mix(in srgb, #fb7185 22%, transparent);
          color: color-mix(in srgb, #fb7185 72%, var(--demo-ink));
        }
        .source-badge.unknown {
          background: color-mix(in srgb, var(--demo-ink) 10%, transparent);
          color: var(--demo-muted);
        }
        .empty-state {
          border-style: dashed;
          border-radius: 12px;
          padding: 26px;
          margin-top: 18px;
        }
        .empty-state h2,
        .empty-state h3 {
          color: var(--demo-ink);
          margin-top: 0;
        }
        .summary-grid {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 12px;
          margin: 10px 0 22px;
        }
        .summary-card {
          border-radius: 10px;
          padding: 12px 14px;
          min-width: 0;
        }
        .summary-label {
          font-size: 0.72rem;
          font-weight: 800;
          letter-spacing: 0.03em;
          text-transform: uppercase;
          margin-bottom: 8px;
        }
        .summary-value {
          display: block;
          color: var(--demo-ink);
          background: color-mix(in srgb, var(--demo-ink) 7%, transparent);
          border: 1px solid var(--demo-line);
          border-radius: 8px;
          padding: 8px;
          font-size: 0.8rem;
          line-height: 1.35;
          max-width: 100%;
          overflow-x: auto;
          white-space: nowrap;
        }
        .table-wrap {
          border-radius: 10px;
          overflow: hidden;
          margin: 8px 0 22px;
        }
        .demo-table {
          width: 100%;
          border-collapse: collapse;
          table-layout: fixed;
          font-size: 0.92rem;
        }
        .demo-table th,
        .demo-table td {
          border-bottom: 1px solid var(--demo-line);
          color: var(--demo-ink);
          padding: 9px 12px;
          text-align: left;
          vertical-align: top;
          overflow-wrap: anywhere;
        }
        .demo-table th {
          color: var(--demo-muted);
          font-size: 0.78rem;
          font-weight: 800;
          text-transform: uppercase;
        }
        @media (max-width: 900px) {
          .selected-card,
          .metric-grid,
          .summary-grid {
            grid-template-columns: 1fr;
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
