"""Tests for localized demo UI texts."""

from __future__ import annotations

from apps.demo.texts import (
    get_texts,
    recommendation_column_names,
    source_explanation,
    source_label,
)


def test_get_texts_supports_english_and_russian() -> None:
    assert get_texts("EN")["tabs"]["similar"]
    assert get_texts("RU")["tabs"]["similar"]


def test_get_texts_falls_back_to_english() -> None:
    assert get_texts("DE")["hero_title"] == get_texts("EN")["hero_title"]


def test_source_label_and_explanation_are_localized() -> None:
    assert "behavioral" in source_label("behavioral", "EN")
    assert "behavioral" in source_label("behavioral", "RU")
    assert "Users interacted" in source_explanation("behavioral", "EN")
    assert "Пользователи" in source_explanation("behavioral", "RU")


def test_unknown_source_is_localized() -> None:
    assert source_label("not_real", "EN")
    assert source_label("not_real", "RU")
    assert source_explanation("not_real", "EN")
    assert source_explanation("not_real", "RU")


def test_recommendation_column_names_are_localized() -> None:
    assert recommendation_column_names("EN")["rank"] == "rank"
    assert recommendation_column_names("RU")["rank"] == "ранг"
