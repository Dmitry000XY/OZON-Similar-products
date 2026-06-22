"""Localized UI texts for the Streamlit demo app."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

LANGUAGES = ("EN", "RU")

TEXTS: dict[str, dict[str, Any]] = {
    "EN": {
        "language": "Language",
        "hero_eyebrow": "Offline recommender presentation",
        "hero_title": "Ozon Similar Products Demo",
        "hero_copy": (
            "Search products, inspect similar items, explain recommendation sources, "
            "and explore the final recommendation graph."
        ),
        "tabs": {
            "similar": "🔎 Similar items",
            "summary": "📊 Run summary",
            "graph": "🕸 Graph view",
            "about": "ℹ️ About",
        },
        "errors": {
            "load_failed_hint": (
                "Build recommendations first or pass an explicit parquet path with "
                "`-- --enriched-path ...`."
            ),
        },
        "similar": {
            "search_label": "Search item by item_id or product name",
            "search_placeholder": "Example: 123456 or milk",
            "random_button": "🎲 Random item",
            "matches": "Matching products",
            "no_matches": "No matching products found. Try another item_id or product name.",
            "empty_title": "Pick a product to start",
            "empty_body": "Use search or the random item button to inspect recommendations.",
            "selected": "Selected item",
            "item_id": "item_id",
            "recommendations": "recommendations",
            "behavioral": "behavioral",
            "fallback": "fallback",
            "top_score": "top score",
            "no_recommendations": (
                "No recommendations found for this item_id in the selected run. "
                "Try another item or use Random item."
            ),
        },
        "recommendation_columns": {
            "rank": "Rank",
            "similar_item_id": "Item ID",
            "similar_item_name": "Recommendation",
            "score": "Score",
            "source": "Raw source",
            "source_label": "Source",
            "explanation": "Explanation",
        },
        "sources": {
            "labels": {
                "behavioral": "Behavioral",
                "fallback_category_type_popular": "Category/type fallback",
                "fallback_category_popular": "Category fallback",
                "fallback_type_popular": "Type fallback",
                "fallback_brand_popular": "Brand fallback",
                "fallback_global_popular": "Popular fallback",
                "unknown": "Unknown source",
            },
            "explanations": {
                "behavioral": "Users interacted with these items in similar sessions",
                "fallback_category_type_popular": "Fallback from same category/type",
                "fallback_category_popular": "Fallback from same category",
                "fallback_type_popular": "Fallback from same type",
                "fallback_brand_popular": "Fallback from same brand",
                "fallback_global_popular": "Popular fallback",
                "unknown": "Unknown source",
            },
        },
        "summary": {
            "cards": {
                "run_id": "run_id",
                "manifest_path": "manifest path",
                "recommendation_artifact": "recommendation artifact",
                "train_window": "train window",
                "top_k": "top_k",
                "created_at": "created_at",
            },
            "not_provided": "not provided",
            "unknown": "unknown",
            "pipeline_rows": "Pipeline rows",
            "stage": "stage",
            "rows": "rows",
            "config": "Config",
            "metrics": "Metrics",
            "metric": "metric",
            "value": "value",
            "metrics_missing": "Metrics file was not found for this run.",
            "metrics_unexpected": "Metrics file exists, but expected demo metrics were not found.",
        },
        "graph": {
            "type": "Graph type",
            "overview": "Overview",
            "ego": "Selected item neighborhood",
            "max_rank": "Max rank",
            "max_edges": "Max edges",
            "max_nodes": "Max nodes",
            "all": "All",
            "sources": "Sources",
            "behavioral": "behavioral",
            "fallback": "fallback",
            "labels": "Labels",
            "label_options": {
                "auto": "Auto",
                "all": "All",
                "important": "Important only",
                "off": "Off",
            },
            "theme": "Theme",
            "theme_options": {
                "auto": "Auto",
                "dark": "Dark",
                "light": "Light",
            },
            "build": "Build graph",
            "reload": "Reload graph",
            "artifact": "Graph artifact",
            "select_first": "Select an item first in the Similar items tab.",
            "select_before_build": "Select an item before building a selected item graph.",
            "select_source": "Select at least one source type.",
            "large_warning": "Large graphs may be slow in browser.",
            "placeholder_title": "Graph visualization placeholder",
            "placeholder_body": (
                "Build a recommendation graph for this run or place a polished Gephi export here:"
            ),
            "expected": "Expected graph artifacts:",
            "expected_items": [
                "recommendations_graph.gexf for Gephi",
                "recommendations_graph.html for browser embedding",
                "recommendations_graph.json for inspection",
            ],
        },
        "about": {
            "title": "What this demo shows",
            "body": (
                "This demo visualizes item-to-item recommendations produced by the offline "
                "Ozon Similar Products pipeline.\n\n"
                "Behavioral recommendations are based on user co-visitation inside sessions. "
                "Fallback recommendations are added when behavioral candidates are not enough. "
                "Scores are produced by the graph/scoring pipeline and ranked into top-K "
                "similar products."
            ),
            "source_title": "Source labels",
            "sources": [
                "behavioral: session co-visitation signal",
                "category/type fallback: popular item from the same category/type",
                "category fallback: popular item from the same category",
                "type fallback: popular item from the same type",
                "brand fallback: popular item from the same brand",
                "popular fallback: global popular item",
            ],
        },
    },
    "RU": {
        "language": "Язык",
        "hero_eyebrow": "Презентация offline-рекомендателя",
        "hero_title": "Демо похожих товаров Ozon",
        "hero_copy": (
            "Ищите товары, смотрите похожие позиции, объясняйте источники рекомендаций "
            "и показывайте итоговый граф связей."
        ),
        "tabs": {
            "similar": "🔎 Похожие товары",
            "summary": "📊 Сводка запуска",
            "graph": "🕸 Граф",
            "about": "ℹ️ О демо",
        },
        "errors": {
            "load_failed_hint": (
                "Сначала постройте рекомендации или передайте parquet явно через "
                "`-- --enriched-path ...`."
            ),
        },
        "similar": {
            "search_label": "Поиск товара по item_id или названию",
            "search_placeholder": "Например: 123456 или молоко",
            "random_button": "🎲 Случайный товар",
            "matches": "Найденные товары",
            "no_matches": "Ничего не найдено. Попробуйте другой item_id или часть названия.",
            "empty_title": "Выберите товар",
            "empty_body": "Воспользуйтесь поиском или кнопкой случайного товара.",
            "selected": "Выбранный товар",
            "item_id": "item_id",
            "recommendations": "рекомендации",
            "behavioral": "behavioral",
            "fallback": "fallback",
            "top_score": "top score",
            "no_recommendations": (
                "Для этого item_id в выбранном запуске рекомендации не найдены. "
                "Попробуйте другой товар или кнопку случайного товара."
            ),
        },
        "recommendation_columns": {
            "rank": "Ранг",
            "similar_item_id": "Item ID",
            "similar_item_name": "Рекомендация",
            "score": "Score",
            "source": "Raw source",
            "source_label": "Источник",
            "explanation": "Объяснение",
        },
        "sources": {
            "labels": {
                "behavioral": "Поведенческий",
                "fallback_category_type_popular": "Fallback категория/тип",
                "fallback_category_popular": "Fallback категория",
                "fallback_type_popular": "Fallback тип",
                "fallback_brand_popular": "Fallback бренд",
                "fallback_global_popular": "Популярный fallback",
                "unknown": "Неизвестный источник",
            },
            "explanations": {
                "behavioral": "Пользователи взаимодействовали с этими товарами в похожих сессиях",
                "fallback_category_type_popular": "Fallback из той же категории и типа",
                "fallback_category_popular": "Fallback из той же категории",
                "fallback_type_popular": "Fallback из того же типа",
                "fallback_brand_popular": "Fallback по тому же бренду",
                "fallback_global_popular": "Популярный fallback-товар",
                "unknown": "Неизвестный источник",
            },
        },
        "summary": {
            "cards": {
                "run_id": "run_id",
                "manifest_path": "manifest path",
                "recommendation_artifact": "recommendation artifact",
                "train_window": "train window",
                "top_k": "top_k",
                "created_at": "created_at",
            },
            "not_provided": "не задано",
            "unknown": "неизвестно",
            "pipeline_rows": "Строки pipeline",
            "stage": "этап",
            "rows": "строки",
            "config": "Config",
            "metrics": "Метрики",
            "metric": "метрика",
            "value": "значение",
            "metrics_missing": "Файл с метриками для этого запуска не найден.",
            "metrics_unexpected": "Файл метрик найден, но ожидаемые demo-метрики отсутствуют.",
        },
        "graph": {
            "type": "Тип графа",
            "overview": "Обзорный граф",
            "ego": "Окрестность выбранного товара",
            "max_rank": "Max rank",
            "max_edges": "Max edges",
            "max_nodes": "Max nodes",
            "all": "All",
            "sources": "Источники",
            "behavioral": "behavioral",
            "fallback": "fallback",
            "labels": "Подписи",
            "label_options": {
                "auto": "Auto",
                "all": "Все",
                "important": "Только важные",
                "off": "Выкл",
            },
            "theme": "Тема",
            "theme_options": {
                "auto": "Auto",
                "dark": "Dark",
                "light": "Light",
            },
            "build": "Построить граф",
            "reload": "Перезагрузить граф",
            "artifact": "Graph artifact",
            "select_first": "Сначала выберите товар на вкладке похожих товаров.",
            "select_before_build": "Выберите товар перед построением ego-графа.",
            "select_source": "Выберите хотя бы один тип источника.",
            "large_warning": "Большой граф может работать медленно в браузере.",
            "placeholder_title": "Заглушка визуализации графа",
            "placeholder_body": (
                "Постройте граф для этого запуска или положите сюда polished Gephi export:"
            ),
            "expected": "Ожидаемые graph artifacts:",
            "expected_items": [
                "recommendations_graph.gexf для Gephi",
                "recommendations_graph.html для встраивания в браузер",
                "recommendations_graph.json для проверки данных",
            ],
        },
        "about": {
            "title": "Что показывает демо",
            "body": (
                "Это демо визуализирует item-to-item рекомендации, построенные offline "
                "pipeline проекта Ozon Similar Products.\n\n"
                "Behavioral-рекомендации основаны на совместных действиях пользователей "
                "внутри сессий. Fallback-рекомендации добавляются, когда behavioral-кандидатов "
                "недостаточно. Score рассчитывается graph/scoring pipeline и затем ранжируется "
                "в top-K похожих товаров."
            ),
            "source_title": "Источники рекомендаций",
            "sources": [
                "behavioral: совместные действия пользователей в сессиях",
                "category/type fallback: популярный товар той же категории и типа",
                "category fallback: популярный товар той же категории",
                "type fallback: популярный товар того же типа",
                "brand fallback: популярный товар того же бренда",
                "popular fallback: глобально популярный товар",
            ],
        },
    },
}


def get_texts(language: str) -> dict[str, Any]:
    """Return localized UI texts, falling back to English for unknown languages."""

    return deepcopy(TEXTS.get(language.upper(), TEXTS["EN"]))


def source_label(source: str | None, language: str = "EN") -> str:
    """Return localized display label for a recommendation source."""

    texts = get_texts(language)
    labels = texts["sources"]["labels"]
    return labels.get(source or "", labels["unknown"])


def source_explanation(source: str | None, language: str = "EN") -> str:
    """Return localized explanation for a recommendation source."""

    texts = get_texts(language)
    explanations = texts["sources"]["explanations"]
    return explanations.get(source or "", explanations["unknown"])


def recommendation_column_names(language: str = "EN") -> dict[str, str]:
    """Return localized display names for recommendation table columns."""

    return dict(get_texts(language)["recommendation_columns"])
