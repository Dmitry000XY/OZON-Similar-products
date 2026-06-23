"""Localized UI texts for the Streamlit demo app."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

LANGUAGES = ("EN", "RU")

TEXTS: dict[str, dict[str, Any]] = {
    "EN": {
        "language": "Language",
        "hero_eyebrow": "Offline recommender demo",
        "hero_title": "Ozon Similar Products Demo",
        "hero_copy": (
            "Search products, inspect similar items, and explain how each recommendation "
            "was produced by the offline pipeline."
        ),
        "tabs": {
            "similar": "🔎 Similar items",
            "summary": "📊 Run summary",
            "about": "ℹ️ About",
        },
        "errors": {
            "load_failed_hint": (
                "Build recommendations first or pass an explicit parquet path with "
                "`-- --enriched-path ...`."
            ),
        },
        "similar": {
            "search_label": "Search by item_id or product name",
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
            "rank": "rank",
            "similar_item_id": "similar item_id",
            "similar_item_name": "similar product",
            "score": "score",
            "source": "source",
            "explanation": "explanation",
        },
        "sources": {
            "labels": {
                "behavioral": "🧠 behavioral",
                "fallback_category_type_popular": "🧩 category/type fallback",
                "fallback_category_popular": "🧩 category fallback",
                "fallback_type_popular": "🧩 type fallback",
                "fallback_brand_popular": "🏷 brand fallback",
                "fallback_global_popular": "🔥 popular fallback",
                "unknown": "❔ unknown source",
            },
            "explanations": {
                "behavioral": "Users interacted with these items in similar sessions",
                "fallback_category_type_popular": "Added from the same category and product type",
                "fallback_category_popular": "Added from the same category",
                "fallback_type_popular": "Added from the same product type",
                "fallback_brand_popular": "Added from the same brand",
                "fallback_global_popular": "Added from globally popular products",
                "unknown": "Unknown recommendation source",
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
            "run_information": "Run information",
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
        "about": {
            "title": "What this demo shows",
            "body": (
                "This demo shows item-to-item recommendations produced by the offline "
                "Ozon Similar Products pipeline.\n\n"
                "The main recommendation source is behavioral co-visitation: products are connected "
                "when users interact with them in similar sessions. If behavioral candidates are not enough, "
                "the fallback layer adds products from related catalogue groups or globally popular products. "
                "The final list is ranked by the graph/scoring pipeline and saved as reusable parquet artifacts."
            ),
            "source_title": "Source labels",
            "sources": [
                "🧠 behavioral — session co-visitation signal",
                "🧩 category/type fallback — popular product from the same category and type",
                "🧩 category fallback — popular product from the same category",
                "🧩 type fallback — popular product from the same type",
                "🏷 brand fallback — popular product from the same brand",
                "🔥 popular fallback — globally popular product",
            ],
        },
    },
    "RU": {
        "language": "Язык",
        "hero_eyebrow": "Демо offline-рекомендателя",
        "hero_title": "Демо похожих товаров Ozon",
        "hero_copy": (
            "Ищите товары, смотрите похожие позиции и объясняйте, почему конкретная "
            "рекомендация появилась в результате работы offline-pipeline."
        ),
        "tabs": {
            "similar": "🔎 Похожие товары",
            "summary": "📊 Сводка запуска",
            "about": "ℹ️ О демо",
        },
        "errors": {
            "load_failed_hint": (
                "Сначала постройте рекомендации или передайте parquet-файл явно через "
                "`-- --enriched-path ...`."
            ),
        },
        "similar": {
            "search_label": "Поиск по item_id или названию товара",
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
            "rank": "ранг",
            "similar_item_id": "item_id похожего товара",
            "similar_item_name": "похожий товар",
            "score": "score",
            "source": "источник",
            "explanation": "объяснение",
        },
        "sources": {
            "labels": {
                "behavioral": "🧠 behavioral",
                "fallback_category_type_popular": "🧩 fallback по категории и типу",
                "fallback_category_popular": "🧩 fallback по категории",
                "fallback_type_popular": "🧩 fallback по типу",
                "fallback_brand_popular": "🏷 fallback по бренду",
                "fallback_global_popular": "🔥 популярный fallback",
                "unknown": "❔ неизвестный источник",
            },
            "explanations": {
                "behavioral": "Пользователи взаимодействовали с этими товарами в похожих сессиях",
                "fallback_category_type_popular": "Товар добавлен из той же категории и того же типа",
                "fallback_category_popular": "Товар добавлен из той же категории",
                "fallback_type_popular": "Товар добавлен из того же типа",
                "fallback_brand_popular": "Товар добавлен по тому же бренду",
                "fallback_global_popular": "Товар добавлен из глобально популярных позиций",
                "unknown": "Неизвестный источник",
            },
        },
        "summary": {
            "cards": {
                "run_id": "run_id",
                "manifest_path": "путь к manifest",
                "recommendation_artifact": "артефакт рекомендаций",
                "train_window": "train window",
                "top_k": "top_k",
                "created_at": "создано",
            },
            "not_provided": "не задано",
            "unknown": "неизвестно",
            "run_information": "Сведения о запуске",
            "pipeline_rows": "Строки по этапам pipeline",
            "stage": "этап",
            "rows": "строки",
            "config": "Конфиг",
            "metrics": "Метрики",
            "metric": "метрика",
            "value": "значение",
            "metrics_missing": "Файл с метриками для этого запуска не найден.",
            "metrics_unexpected": "Файл метрик найден, но ожидаемые demo-метрики отсутствуют.",
        },
        "about": {
            "title": "Что показывает демо",
            "body": (
                "Это демо показывает item-to-item рекомендации, построенные offline-pipeline "
                "проекта Ozon Similar Products.\n\n"
                "Основной источник рекомендаций — поведенческие связи между товарами: товары "
                "считаются похожими, если пользователи взаимодействовали с ними в похожих сессиях. "
                "Если таких кандидатов недостаточно, fallback-слой добавляет товары из близких "
                "каталожных групп или глобально популярные позиции. Финальный список ранжируется "
                "scoring-слоем и сохраняется в parquet-артефакты."
            ),
            "source_title": "Источники рекомендаций",
            "sources": [
                "🧠 behavioral — связь по пользовательскому поведению в сессиях",
                "🧩 fallback по категории и типу — популярный товар из той же категории и типа",
                "🧩 fallback по категории — популярный товар из той же категории",
                "🧩 fallback по типу — популярный товар того же типа",
                "🏷 fallback по бренду — популярный товар того же бренда",
                "🔥 популярный fallback — глобально популярный товар",
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


def recommendation_column_names(
    language: str = "EN",
) -> dict[Any, Any] | dict[str, Any] | dict[str, str] | dict[bytes, bytes]:
    """Return localized display names for recommendation table columns."""

    return dict(get_texts(language)["recommendation_columns"])
