"""Streamlit UI for browsing similar product recommendations."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

import polars as pl
import streamlit as st
import streamlit.components.v1 as components

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
    source_group,
)
from apps.demo.texts import (  # noqa: E402
    LANGUAGES,
    get_texts,
    recommendation_column_names,
    source_explanation,
    source_label,
)
from ozon_similar_products.visualization import (  # noqa: E402
    RecommendationGraphConfig,
    export_recommendation_graph,
)

PAGE_TITLE = "Ozon Similar Products Demo"
DEFAULT_MANIFEST_PATH = Path("outputs/latest/manifest.json")
GRAPH_HEIGHT = 940


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

    tab_similar, tab_summary, tab_graph, tab_about = st.tabs(
        [
            texts["tabs"]["similar"],
            texts["tabs"]["summary"],
            texts["tabs"]["graph"],
            texts["tabs"]["about"],
        ]
    )
    with tab_similar:
        _render_similar_items_tab(state=state, top_k=args.top_k, language=language)
    with tab_summary:
        _render_run_summary_tab(state=state, top_k=args.top_k, language=language)
    with tab_graph:
        _render_graph_tab(state=state, language=language)
    with tab_about:
        _render_about_tab(language=language)


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


def _language_selector() -> str:
    if "demo_language" not in st.session_state:
        st.session_state.demo_language = "EN"
    _left, right = st.columns([0.82, 0.18], vertical_alignment="center")
    with right:
        language = st.radio(
            "Language",
            LANGUAGES,
            index=LANGUAGES.index(st.session_state.demo_language),
            horizontal=True,
            label_visibility="collapsed",
            key="demo_language_radio",
        )
    st.session_state.demo_language = str(language)
    return st.session_state.demo_language


def _render_hero(texts: Mapping[str, Any]) -> None:
    st.markdown(
        f"""
        <section class="demo-hero">
          <div>
            <p class="eyebrow">{_html_escape(texts["hero_eyebrow"])}</p>
            <h1>{_html_escape(texts["hero_title"])}</h1>
            <p class="hero-copy">{_html_escape(texts["hero_copy"])}</p>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_similar_items_tab(
        *,
        state: dict[str, Any],
        top_k: int,
        language: str,
) -> None:
    texts = get_texts(language)
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
        _render_empty_state(similar_texts["empty_title"], similar_texts["empty_body"])

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

    _render_selected_item_card(selected_row, recommendations, language=language)
    if recommendations.is_empty():
        st.info(similar_texts["no_recommendations"])
        return

    dist = source_distribution(recommendations)
    _render_source_badges(dist, language=language)
    st.dataframe(
        _display_recommendations(recommendations, language=language),
        use_container_width=True,
        hide_index=True,
        height=600,
    )


def _render_selected_item_card(
        item: dict[str, Any],
        recommendations: pl.DataFrame,
        *,
        language: str,
) -> None:
    texts = get_texts(language)
    similar_texts = texts["similar"]
    distribution = source_distribution(recommendations)
    distribution_rows = {row["source"]: int(row["count"]) for row in distribution.to_dicts()}
    behavioral_count = distribution_rows.get("behavioral", 0)
    fallback_count = sum(
        count for source, count in distribution_rows.items() if str(source).startswith("fallback_")
    )
    top_score = recommendations["score"].max() if not recommendations.is_empty() else None
    item_name = _display_name(item.get("item_name"))
    item_id = str(item.get("item_id"))

    st.markdown(
        f"""
        <section class="selected-card">
          <div class="selected-main">
            <p class="eyebrow">{_html_escape(similar_texts["selected"])}</p>
            <h2 title="{_html_escape(item_name)}">{_html_escape(item_name)}</h2>
            <p class="item-meta">{_html_escape(similar_texts["item_id"])}: {_html_escape(item_id)}</p>
          </div>
          <div class="metric-grid">
            <div class="mini-metric"><span>{_html_escape(similar_texts["recommendations"])}</span><strong>{recommendations.height}</strong></div>
            <div class="mini-metric"><span>{_html_escape(similar_texts["behavioral"])}</span><strong>{behavioral_count}</strong></div>
            <div class="mini-metric"><span>{_html_escape(similar_texts["fallback"])}</span><strong>{fallback_count}</strong></div>
            <div class="mini-metric"><span>{_html_escape(similar_texts["top_score"])}</span><strong>{_format_score(top_score)}</strong></div>
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
            f"{_html_escape(source_label(source, language))}: {count}"
            "</span>"
        )
    st.markdown(f'<div class="badge-row">{"".join(badges)}</div>', unsafe_allow_html=True)


def _display_recommendations(recommendations: pl.DataFrame, *, language: str) -> pl.DataFrame:
    column_names = recommendation_column_names(language)
    return (
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
        .select(
            "rank",
            "similar_item_id",
            "similar_item_name",
            "score",
            "source",
            "explanation",
        )
        .rename(column_names)
    )


def _render_run_summary_tab(*, state: dict[str, Any], top_k: int, language: str) -> None:
    texts = get_texts(language)
    summary_texts = texts["summary"]
    manifest: dict[str, Any] | None = state["manifest"]
    manifest_path: Path | None = state["manifest_path"]
    recommendation_path: Path = state["recommendation_path"]
    run_dir: Path = state["run_dir"]

    cards = summary_texts["cards"]
    summary = [
        (cards["run_id"], _manifest_value(manifest, "run_id", summary_texts["unknown"]), True),
        (
            cards["manifest_path"],
            manifest_path.as_posix() if manifest_path else summary_texts["not_provided"],
            True,
        ),
        (cards["recommendation_artifact"], recommendation_path.as_posix(), True),
        (cards["train_window"], _train_window(manifest), False),
        (cards["top_k"], _manifest_value(manifest, "top_k", top_k), False),
        (
            cards["created_at"],
            _manifest_value(
                manifest,
                "created_at",
                _manifest_value(manifest, "generated_at", "—"),
            ),
            False,
        ),
    ]
    _render_summary_cards(summary)

    rows = manifest.get("rows") if isinstance(manifest, dict) else None
    if isinstance(rows, dict) and rows:
        st.subheader(summary_texts["pipeline_rows"])
        st.dataframe(
            pl.DataFrame(
                {
                    summary_texts["stage"]: list(rows.keys()),
                    summary_texts["rows"]: [str(value) for value in rows.values()],
                }
            ),
            use_container_width=True,
            hide_index=True,
            height=300,
        )

    config_path = _config_path(manifest)
    if config_path is not None:
        st.markdown(f"**{_html_escape(summary_texts['config'])}:** `{config_path}`")

    metrics = _load_metrics(run_dir)
    if metrics is None:
        st.info(summary_texts["metrics_missing"])
        return

    st.subheader(summary_texts["metrics"])
    flattened = _flatten_metrics(metrics)
    metric_rows = [
        (key, _format_metric_value(flattened.get(key, "—")))
        for key in METRIC_KEYS
        if key in flattened
    ]
    if metric_rows:
        _render_metrics_table(
            metric_rows,
            metric_label=summary_texts["metric"],
            value_label=summary_texts["value"],
        )
    else:
        st.info(summary_texts["metrics_unexpected"])


def _render_summary_cards(cards: list[tuple[str, Any, bool]]) -> None:
    html_cards = []
    for label, value, is_path_like in cards:
        value_text = str(value)
        value_html = (
            f'<code class="summary-value scrollable">{_html_escape(value_text)}</code>'
            if is_path_like
            else f'<div class="summary-value">{_html_escape(value_text)}</div>'
        )
        html_cards.append(
            f"""
            <div class="summary-card">
              <div class="summary-label">{_html_escape(label)}</div>
              {value_html}
            </div>
            """
        )
    st.markdown(f'<div class="summary-grid">{"".join(html_cards)}</div>', unsafe_allow_html=True)


def _render_metrics_table(
        rows: list[tuple[str, str]],
        *,
        metric_label: str,
        value_label: str,
) -> None:
    body = "".join(
        f"<tr><td>{_html_escape(metric)}</td><td>{_html_escape(value)}</td></tr>"
        for metric, value in rows
    )
    st.markdown(
        f"""
        <table class="metrics-table">
          <thead><tr><th>{_html_escape(metric_label)}</th><th>{_html_escape(value_label)}</th></tr></thead>
          <tbody>{body}</tbody>
        </table>
        """,
        unsafe_allow_html=True,
    )


def _render_graph_tab(*, state: dict[str, Any], language: str) -> None:
    texts = get_texts(language)
    graph_texts = texts["graph"]
    run_dir: Path = state["run_dir"]
    recommendation_path: Path = state["recommendation_path"]
    selected_item_id = st.session_state.get("selected_item_id")

    graph_types = {
        "overview": graph_texts["overview"],
        "ego": graph_texts["ego"],
    }
    default_mode = "ego" if selected_item_id is not None else "overview"
    graph_type_label = st.radio(
        graph_texts["type"],
        list(graph_types.values()),
        index=list(graph_types).index(default_mode),
        horizontal=True,
    )
    mode = _graph_mode_for_label(graph_types, graph_type_label)
    is_ego = mode == "ego"

    row_one = st.columns([0.18, 0.18, 0.18, 0.24, 0.22], vertical_alignment="bottom")
    with row_one[0]:
        max_rank = st.selectbox(graph_texts["max_rank"], [3, 5, 10, 20, 50], index=1)
    with row_one[1]:
        max_edges = st.selectbox(
            graph_texts["max_edges"],
            [1000, 2000, 5000, 10000, graph_texts["all"]],
            index=0,
        )
    with row_one[2]:
        max_nodes = st.selectbox(
            graph_texts["max_nodes"],
            [500, 1000, 3000, graph_texts["all"]],
            index=3,
        )
    with row_one[3]:
        labels_options = graph_texts["label_options"]
        labels_label = st.selectbox(
            graph_texts["labels"],
            [labels_options["auto"], labels_options["important"], labels_options["all"], labels_options["off"]],
            index=1,
        )
    with row_one[4]:
        theme_options = graph_texts["theme_options"]
        theme_label = st.selectbox(
            graph_texts["theme"],
            [theme_options["auto"], theme_options["dark"], theme_options["light"]],
            index=0,
        )

    row_two = st.columns([0.16, 0.16, 0.28, 0.2, 0.2], vertical_alignment="bottom")
    with row_two[0]:
        include_behavioral = st.checkbox(graph_texts["behavioral"], value=True)
    with row_two[1]:
        include_fallback = st.checkbox(graph_texts["fallback"], value=True)
    with row_two[2]:
        build_clicked = st.button(graph_texts["build"], use_container_width=True)
    with row_two[3]:
        reload_clicked = st.button(graph_texts["reload"], use_container_width=True)

    optional_max_edges = _optional_limit(max_edges, all_label=graph_texts["all"])
    optional_max_nodes = _optional_limit(max_nodes, all_label=graph_texts["all"])
    if optional_max_edges is None or optional_max_nodes is None:
        st.warning(graph_texts["large_warning"])

    if is_ego and selected_item_id is None:
        st.info(graph_texts["select_first"])

    if build_clicked:
        if is_ego and selected_item_id is None:
            st.warning(graph_texts["select_before_build"])
        elif not include_behavioral and not include_fallback:
            st.warning(graph_texts["select_source"])
        else:
            output_dir = _graph_output_dir(
                run_dir=run_dir,
                selected_item_id=selected_item_id if is_ego else None,
            )
            export_recommendation_graph(
                recommendation_path=recommendation_path,
                output_dir=output_dir,
                config=RecommendationGraphConfig(
                    mode=mode,
                    selected_item_id=selected_item_id if is_ego else None,
                    max_rank=int(max_rank),
                    max_edges=optional_max_edges,
                    max_nodes=optional_max_nodes,
                    include_behavioral=include_behavioral,
                    include_fallback=include_fallback,
                    labels_mode=_labels_mode_for_label(labels_options, labels_label),
                    theme=_theme_for_label(theme_options, theme_label),
                ),
                manifest_path=state["manifest_path"],
            )
            st.rerun()

    if reload_clicked:
        st.rerun()

    graph_html = _find_graph_html(run_dir, selected_item_id=selected_item_id if is_ego else None)
    if graph_html is not None:
        st.caption(f"{graph_texts['artifact']}: `{graph_html.as_posix()}`")
        components.html(graph_html.read_text(encoding="utf-8"), height=GRAPH_HEIGHT, scrolling=False)
        return

    run_id = _manifest_value(state["manifest"], "run_id", "<run_id>")
    items = "".join(f"<li>{_html_escape(item)}</li>" for item in graph_texts["expected_items"])
    st.markdown(
        f"""
        <section class="empty-state graph-placeholder">
          <h2>{_html_escape(graph_texts["placeholder_title"])}</h2>
          <p>{_html_escape(graph_texts["placeholder_body"])}</p>
          <code>outputs/runs/{_html_escape(str(run_id))}/demo/gephi/index.html</code>
          <p class="muted">{_html_escape(graph_texts["expected"])}</p>
          <ul>{items}</ul>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_about_tab(*, language: str) -> None:
    about = get_texts(language)["about"]
    source_items = "\n".join(f"- {item}" for item in about["sources"])
    st.markdown(
        f"""
        ### {about["title"]}

        {about["body"]}

        ### {about["source_title"]}

        {source_items}
        """
    )


def _render_empty_state(title: str, body: str) -> None:
    st.markdown(
        f"""
        <div class="empty-state">
          <h3>{_html_escape(title)}</h3>
          <p>{_html_escape(body)}</p>
        </div>
        """,
        unsafe_allow_html=True,
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


def _find_graph_html(run_dir: Path, *, selected_item_id: Any | None = None) -> Path | None:
    candidate_paths = [run_dir / "demo" / "gephi" / "index.html"]
    if selected_item_id is not None:
        candidate_paths.append(
            _graph_output_dir(run_dir=run_dir, selected_item_id=selected_item_id) / "ego_graph.html"
        )
    candidate_paths.extend(
        [
            run_dir / "demo" / "graph" / "recommendations_graph.html",
            run_dir / "demo" / "graph.html",
            Path("apps/demo/assets/graph/index.html"),
        ]
    )
    for path in candidate_paths:
        if path.exists():
            return path
    return None


def _graph_output_dir(run_dir: Path, selected_item_id: Any | None = None) -> Path:
    graph_dir = run_dir / "demo" / "graph"
    if selected_item_id is None:
        return graph_dir
    return graph_dir / "ego" / f"item_id={_safe_item_id_path(selected_item_id)}"


def _safe_item_id_path(value: Any) -> str:
    text = str(value)
    safe = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_" for character in text
    )
    return safe or "unknown"


def _display_name(value: Any) -> str:
    return "None" if value is None else str(value)


def _format_item_option(row: dict[str, Any]) -> str:
    return f"{row['item_id']} · {_display_name(row.get('item_name'))}"


def _format_score(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.4f}"


def _format_metric_value(value: Any) -> str:
    if value is None or value == "—":
        return "—"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{parsed:.6f}".rstrip("0").rstrip(".")


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


def _key_for_label(options: Mapping[str, str], selected_label: str | None) -> str:
    for key, label in options.items():
        if label == selected_label:
            return key
    return next(iter(options))


def _graph_mode_for_label(
        options: Mapping[str, str],
        selected_label: str | None,
) -> Literal["overview", "ego"]:
    key = _key_for_label(options, selected_label)
    if key not in {"overview", "ego"}:
        return "overview"
    return cast(Literal["overview", "ego"], key)


def _labels_mode_for_label(
        options: Mapping[str, str],
        selected_label: str | None,
) -> Literal["auto", "important", "all", "off"]:
    key = _key_for_label(options, selected_label)
    if key not in {"auto", "important", "all", "off"}:
        return "important"
    return cast(Literal["auto", "important", "all", "off"], key)


def _theme_for_label(
        options: Mapping[str, str],
        selected_label: str | None,
) -> Literal["auto", "dark", "light"]:
    key = _key_for_label(options, selected_label)
    if key not in {"auto", "dark", "light"}:
        return "auto"
    return cast(Literal["auto", "dark", "light"], key)


def _optional_limit(value: Any, *, all_label: str) -> int | None:
    if value == all_label:
        return None
    return int(value)


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        :root {
          --demo-bg: #ffffff;
          --demo-panel: #f8fafc;
          --demo-card: #ffffff;
          --demo-soft: #eef3f8;
          --demo-ink: #17202a;
          --demo-muted: #667085;
          --demo-line: #d7dde8;
          --demo-accent: #1f7a8c;
          --demo-shadow: rgba(16, 24, 40, 0.08);
          --demo-code-bg: #edf2f7;
        }
        @media (prefers-color-scheme: dark) {
          :root {
            --demo-bg: #0e1117;
            --demo-panel: #151922;
            --demo-card: #1b202b;
            --demo-soft: #111827;
            --demo-ink: #f4f6fb;
            --demo-muted: #a8b3c7;
            --demo-line: #2b3342;
            --demo-accent: #5cc8d7;
            --demo-shadow: rgba(0, 0, 0, 0.28);
            --demo-code-bg: #111827;
          }
        }
        .stApp {
          background: var(--demo-bg);
        }
        .block-container {
          padding-top: 1.4rem;
          padding-bottom: 3rem;
        }
        .demo-hero,
        .selected-card,
        .summary-card,
        .empty-state {
          background: var(--demo-card);
          border: 1px solid var(--demo-line);
          border-radius: 8px;
          color: var(--demo-ink);
          box-shadow: 0 12px 32px var(--demo-shadow);
        }
        .demo-hero {
          padding: 24px 28px;
          margin-bottom: 18px;
        }
        .demo-hero h1 {
          color: var(--demo-ink);
          font-size: 2.35rem;
          line-height: 1.08;
          margin: 0.15rem 0 0.55rem;
          letter-spacing: 0;
        }
        .hero-copy,
        .muted,
        .empty-state p,
        .item-meta,
        .summary-label {
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
          grid-template-columns: minmax(300px, 1.25fr) minmax(360px, 1.75fr);
          align-items: stretch;
          padding: 22px;
          margin: 18px 0;
        }
        .selected-main h2 {
          color: var(--demo-ink);
          font-size: 2rem;
          line-height: 1.18;
          margin: 0.3rem 0 0.45rem;
          letter-spacing: 0;
          max-height: 4.8rem;
          overflow: hidden;
        }
        .item-meta {
          font-size: 0.92rem;
          margin: 0;
        }
        .metric-grid {
          display: grid;
          gap: 10px;
          grid-template-columns: repeat(4, minmax(120px, 1fr));
        }
        .mini-metric {
          background: var(--demo-panel);
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
          font-size: 1.3rem;
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
          background: color-mix(in srgb, #159895 18%, transparent);
          color: var(--demo-ink);
        }
        .source-badge.fallback {
          background: color-mix(in srgb, #64748b 20%, transparent);
          color: var(--demo-ink);
        }
        .source-badge.brand {
          background: color-mix(in srgb, #c026d3 18%, transparent);
          color: var(--demo-ink);
        }
        .source-badge.popular {
          background: color-mix(in srgb, #ef4444 18%, transparent);
          color: var(--demo-ink);
        }
        .source-badge.unknown {
          background: var(--demo-panel);
          color: var(--demo-muted);
        }
        .summary-grid {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          margin: 6px 0 22px;
        }
        .summary-card {
          min-width: 0;
          padding: 14px;
        }
        .summary-label {
          font-size: 0.76rem;
          font-weight: 700;
          margin-bottom: 8px;
          text-transform: uppercase;
        }
        .summary-value {
          color: var(--demo-ink);
          font-size: 0.95rem;
          line-height: 1.4;
          overflow-wrap: anywhere;
        }
        .summary-value.scrollable {
          background: var(--demo-code-bg);
          border: 1px solid var(--demo-line);
          border-radius: 6px;
          display: block;
          max-width: 100%;
          overflow-x: auto;
          padding: 8px;
          white-space: nowrap;
        }
        .metrics-table {
          border-collapse: collapse;
          border: 1px solid var(--demo-line);
          border-radius: 8px;
          color: var(--demo-ink);
          display: table;
          overflow: hidden;
          width: 100%;
        }
        .metrics-table th,
        .metrics-table td {
          border-bottom: 1px solid var(--demo-line);
          padding: 9px 12px;
          text-align: left;
        }
        .metrics-table th {
          background: var(--demo-panel);
          color: var(--demo-muted);
          font-size: 0.78rem;
          text-transform: uppercase;
        }
        .metrics-table td:last-child {
          font-variant-numeric: tabular-nums;
        }
        .empty-state {
          padding: 24px;
          margin-top: 18px;
        }
        .empty-state h2,
        .empty-state h3 {
          color: var(--demo-ink);
          margin-top: 0;
          letter-spacing: 0;
        }
        .graph-placeholder code {
          background: var(--demo-code-bg);
          border: 1px solid var(--demo-line);
          border-radius: 6px;
          color: var(--demo-ink);
          display: inline-block;
          margin: 8px 0 12px;
          max-width: 100%;
          overflow-x: auto;
          padding: 8px 10px;
        }
        div[data-testid="stDataFrame"] {
          color: var(--demo-ink);
        }
        @media (max-width: 1000px) {
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
