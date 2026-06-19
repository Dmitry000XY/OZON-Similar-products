"""Tests for tuning search-space helpers."""

import pytest

from ozon_similar_products.cli import run_tune


def test_set_by_dot_path_updates_nested_config_without_mutation() -> None:
    config = {"scoring": {"business_weights": {"click": 3.0}}, "topk": {"top_k": 20}}

    updated = run_tune.set_by_dot_path(config, "scoring.business_weights.click", 5.0)

    assert updated["scoring"]["business_weights"]["click"] == 5.0
    assert config["scoring"]["business_weights"]["click"] == 3.0


def test_generate_grid_trials_from_choice_search_space() -> None:
    search_space = {
        "parameters": {
            "topk.top_k": {"type": "choice", "values": [10, 20]},
            "scoring.min_unique_users": {"type": "choice", "values": [1, 2]},
        }
    }

    trials = run_tune.generate_grid_trials(search_space)

    assert trials == [
        {"topk.top_k": 10, "scoring.min_unique_users": 1},
        {"topk.top_k": 10, "scoring.min_unique_users": 2},
        {"topk.top_k": 20, "scoring.min_unique_users": 1},
        {"topk.top_k": 20, "scoring.min_unique_users": 2},
    ]


def test_select_trials_random_limits_without_replacement() -> None:
    search_space = {
        "parameters": {
            "a": {"type": "choice", "values": [1, 2, 3]},
        }
    }

    trials = run_tune.select_trials(search_space, strategy="random", max_trials=2, seed=1)

    assert len(trials) == 2
    assert len({trial["a"] for trial in trials}) == 2


def test_successive_halving_is_clear_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="successive_halving"):
        run_tune.select_trials(
            {"parameters": {"a": {"type": "choice", "values": [1]}}},
            strategy="successive_halving",
            max_trials=1,
        )


def test_best_trial_row_uses_primary_metric_tie_breakers_and_popularity_penalty() -> None:
    search_space = {
        "objective": {
            "primary_metric": "to_cart_hit_rate_at_k",
            "tie_breakers": ["ndcg_at_k", "coverage_at_k"],
        }
    }
    rows = [
        {
            "trial_id": "trial_0001",
            "to_cart_hit_rate_at_k": 0.5,
            "ndcg_at_k": 0.8,
            "coverage_at_k": 0.1,
            "popularity_bias_at_k": 0.2,
        },
        {
            "trial_id": "trial_0002",
            "to_cart_hit_rate_at_k": 0.5,
            "ndcg_at_k": 0.8,
            "coverage_at_k": 0.2,
            "popularity_bias_at_k": 0.9,
        },
    ]

    best = run_tune.best_trial_row(rows, search_space)

    assert best["trial_id"] == "trial_0002"
