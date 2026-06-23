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
RECOMMENDATION_TABLE_ROWS = 20


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
    tab_similar, tab_summary, tab_about = st.tabs([tabs["similar"], tabs["summary"], tabs["about"]])
    with tab_similar:
        _render_similar_items_tab(
            state=state,
            top_k=min(args.top_k, RECOMMENDATION_TABLE_ROWS),
            texts=texts,
            language=language,
        )
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
    with st.container(border=True):
        st.caption(str(texts["hero_eyebrow"]).upper())
        st.title(str(texts["hero_title"]))
        st.write(str(texts["hero_copy"]))


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
        with st.container(border=True):
            st.subheader(similar_texts["empty_title"])
            st.write(similar_texts["empty_body"])

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
        height=_recommendation_table_height(RECOMMENDATION_TABLE_ROWS),
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

    with st.container(border=True):
        left, right = st.columns([0.44, 0.56], vertical_alignment="center")
        with left:
            st.caption(str(similar_texts["selected"]).upper())
            st.subheader(item_name)
            st.caption(f"{similar_texts['item_id']}: {item_id}")
        with right:
            cols = st.columns(4)
            metric_values = [
                (similar_texts["recommendations"], recommendations.height),
                (similar_texts["behavioral"], behavioral_count),
                (similar_texts["fallback"], fallback_count),
                (similar_texts["top_score"], _format_score(top_score)),
            ]
            for column, (label, value) in zip(cols, metric_values, strict=True):
                with column:
                    with st.container(border=True):
                        st.caption(str(label))
                        st.markdown(f"**{value}**")


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
    display = recommendations.with_columns(
        pl.col("score").round(6).alias("score"),
        pl.col("source")
        .fill_null("")
        .map_elements(lambda value: source_label(value, language), return_dtype=pl.Utf8)
        .alias("source"),
        pl.col("source")
        .fill_null("")
        .map_elements(lambda value: source_explanation(value, language), return_dtype=pl.Utf8)
        .alias("explanation"),
    ).select("rank", "similar_item_id", "similar_item_name", "score", "source", "explanation")
    return display.rename(recommendation_column_names(language))


def _render_run_summary_tab(*, state: dict[str, Any], top_k: int, texts: Mapping[str, Any]) -> None:
    summary_texts = texts["summary"]
    manifest: dict[str, Any] | None = state["manifest"]
    manifest_path: Path | None = state["manifest_path"]
    recommendation_path: Path = state["recommendation_path"]
    run_dir: Path = state["run_dir"]

    st.subheader(summary_texts.get("run_information", "Run information"))

    summary = {
        summary_texts["cards"]["run_id"]: _manifest_value(
            manifest, "run_id", summary_texts["unknown"]
        ),
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
        _render_dataframe_table(row_data)

    config_path = _config_path(manifest)
    if config_path is not None:
        st.markdown(f"**{summary_texts['config']}:** `{config_path}`")

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
        _render_dataframe_table(metric_rows)
    else:
        st.info(summary_texts["metrics_unexpected"])


def _render_summary_cards(summary: Mapping[str, Any]) -> None:
    items = list(summary.items())
    for start in range(0, len(items), 3):
        columns = st.columns(3)
        for column, (label, value) in zip(columns, items[start : start + 3], strict=False):
            with column:
                with st.container(border=True):
                    st.caption(str(label))
                    st.code(str(value), language=None)


def _render_dataframe_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    st.dataframe(
        pl.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        height=_compact_table_height(len(rows)),
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
    except TypeError, ValueError:
        return str(value)


def _format_int_like(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except TypeError, ValueError:
        return str(value)


def _recommendation_table_height(row_count: int) -> int:
    return 737 if row_count >= RECOMMENDATION_TABLE_ROWS else _compact_table_height(row_count)


def _compact_table_height(row_count: int) -> int:
    return min(380, max(112, 39 * row_count + 5))


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        .language-offset { height: 0.85rem; }
        .block-container {
          padding-top: 1.4rem;
          padding-bottom: 3rem;
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
          background: rgba(20, 184, 166, 0.18);
          color: #14b8a6;
        }
        .source-badge.fallback {
          background: rgba(148, 163, 184, 0.18);
          color: #94a3b8;
        }
        .source-badge.brand {
          background: rgba(245, 158, 11, 0.18);
          color: #f59e0b;
        }
        .source-badge.popular {
          background: rgba(251, 113, 133, 0.18);
          color: #fb7185;
        }
        .source-badge.unknown {
          background: rgba(148, 163, 184, 0.18);
          color: #94a3b8;
        }
        [data-testid="stCodeBlock"] pre,
        [data-testid="stCodeBlock"] code {
          white-space: pre-wrap !important;
          overflow-wrap: anywhere !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
