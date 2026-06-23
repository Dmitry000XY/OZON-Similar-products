"""Tests for Streamlit demo data helpers."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
from apps.demo.demo_data import (
    choose_random_item,
    find_recommendation_path,
    normalize_recommendations,
    recommendations_for_item,
    search_items,
    source_explanation,
)
from apps.demo.texts import (
    get_texts,
    recommendation_column_names,
    source_label,
)
from apps.demo.texts import (
    source_explanation as localized_source_explanation,
)


def test_normalize_recommendations_for_enriched_like_frame() -> None:
    frame = pl.DataFrame(
        {
            "item_id": [2, 1],
            "item_name": ["Tea", "Milk"],
            "similar_item_id": [20, 10],
            "similar_item_name": ["Sugar", "Cookie"],
            "rank": [2, 1],
            "score": [0.5, 0.9],
            "source": ["fallback_global_popular", "behavioral"],
        }
    )

    normalized = normalize_recommendations(frame)

    assert normalized.columns == [
        "item_id",
        "item_name",
        "similar_item_id",
        "similar_item_name",
        "rank",
        "score",
        "source",
    ]
    assert normalized["item_id"].to_list() == [1, 2]


def test_normalize_recommendations_for_detailed_like_frame_without_names() -> None:
    frame = pl.DataFrame(
        {
            "item_id": [1],
            "similar_item_id": [10],
            "rank": [1],
            "score": [0.9],
            "source": ["behavioral"],
        }
    )

    normalized = normalize_recommendations(frame)

    assert normalized["item_name"].to_list() == [None]
    assert normalized["similar_item_name"].to_list() == [None]


def test_search_items_exact_item_id() -> None:
    catalog = _catalog()

    result = search_items(catalog, "102")

    assert result.select("item_id").to_series().to_list() == [102]


def test_search_items_substring_by_name() -> None:
    catalog = _catalog()

    result = search_items(catalog, "milk")

    assert result.select("item_id").to_series().to_list() == [101]


def test_search_items_is_case_insensitive() -> None:
    catalog = _catalog()

    result = search_items(catalog, "BLACK")

    assert result.select("item_id").to_series().to_list() == [102]


def test_search_items_empty_query_returns_empty_frame() -> None:
    catalog = _catalog()

    assert search_items(catalog, "").is_empty()


def test_choose_random_item_prefers_non_null_name() -> None:
    catalog = pl.DataFrame(
        {
            "item_id": [1, 2],
            "item_name": [None, "Named item"],
            "recommendation_count": [1, 1],
        }
    )

    selected = choose_random_item(catalog, seed=1)

    assert selected is not None
    assert selected["item_id"] == 2


def test_choose_random_item_handles_empty_catalog() -> None:
    catalog = pl.DataFrame(
        schema={
            "item_id": pl.Int64,
            "item_name": pl.Utf8,
            "recommendation_count": pl.UInt32,
        }
    )

    assert choose_random_item(catalog) is None


def test_recommendations_for_item_returns_top_k_sorted_by_rank() -> None:
    frame = normalize_recommendations(
        pl.DataFrame(
            {
                "item_id": [1, 1, 1],
                "similar_item_id": [12, 11, 13],
                "rank": [2, 1, 3],
                "score": [0.7, 0.8, 0.6],
                "source": ["behavioral", "behavioral", "fallback_global_popular"],
            }
        )
    )

    result = recommendations_for_item(frame, 1, top_k=2)

    assert result.select("similar_item_id").to_series().to_list() == [11, 12]


def test_source_explanation_maps_known_and_unknown_sources() -> None:
    assert source_explanation("behavioral") == "Users interacted with these items in similar sessions"
    assert source_explanation("not_a_real_source") == "Unknown source"
    assert source_explanation(None) == "Unknown source"


def test_localized_source_texts_cover_english_and_russian() -> None:
    assert "behavioral" in source_label("behavioral", "EN")
    assert "behavioral" in source_label("behavioral", "RU")
    assert "sessions" in localized_source_explanation("behavioral", "EN")
    assert "сессиях" in localized_source_explanation("behavioral", "RU")
    assert localized_source_explanation("not_a_real_source", "RU") == "Неизвестный источник"


def test_recommendation_column_names_are_localized() -> None:
    english = recommendation_column_names("EN")
    russian = recommendation_column_names("RU")

    assert english["similar_item_name"] == "similar product"
    assert russian["similar_item_name"] == "похожий товар"
    assert english["source"] == "source"
    assert russian["source"] == "источник"


def test_get_texts_falls_back_to_english_and_returns_copy() -> None:
    first = get_texts("EN")
    second = get_texts("DE")

    first["hero_title"] = "changed"
    assert second["hero_title"] == "Ozon Similar Products Demo"


def test_find_recommendation_path_prefers_explicit_enriched_path(tmp_path: Path) -> None:
    enriched_path = tmp_path / "custom" / "enriched.parquet"
    enriched_path.parent.mkdir()
    enriched_path.touch()

    result = find_recommendation_path(
        manifest_path=None,
        enriched_path=enriched_path,
        detailed_path=None,
    )

    assert result == enriched_path


def test_find_recommendation_path_supports_manifest_relative_path(tmp_path: Path) -> None:
    run_dir = tmp_path / "outputs" / "runs" / "run_001"
    recommendation_path = run_dir / "recommendations" / "enriched.parquet"
    recommendation_path.parent.mkdir(parents=True)
    recommendation_path.touch()
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps({"paths": {"enriched_recommendations_path": "recommendations/enriched.parquet"}}),
        encoding="utf-8",
    )

    result = find_recommendation_path(
        manifest_path=manifest_path,
        enriched_path=None,
        detailed_path=None,
    )

    assert result == recommendation_path


def _catalog() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "item_id": [101, 102, 103],
            "item_name": ["Fresh Milk 2.5%", "Black Tea", None],
            "recommendation_count": [3, 4, 1],
        }
    )
