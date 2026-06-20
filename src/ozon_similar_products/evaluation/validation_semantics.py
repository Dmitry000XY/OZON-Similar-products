"""Stable validation semantics for offline evaluation ground truth."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from ozon_similar_products.data import schemas


def _mapping_copy(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return deepcopy(dict(value))
    return {}


def _normalized_action_types(config: Mapping[str, Any]) -> list[str]:
    events_config = config.get("events", {})
    if not isinstance(events_config, Mapping):
        return list(schemas.ITEM_SIGNAL_TYPES)

    action_types = events_config.get("item_action_types", schemas.ITEM_SIGNAL_TYPES)
    if isinstance(action_types, str):
        return [action_types]
    if isinstance(action_types, Sequence):
        return [str(action_type) for action_type in action_types]
    return list(schemas.ITEM_SIGNAL_TYPES)


def validation_ground_truth_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a sanitized config for stable validation pair semantics.

    Validation ground truth should keep stable session/pair construction
    semantics across trials. Tuned graph decay settings affect train
    recommendations, but they must not affect the validation target.
    """
    pipeline_config = _mapping_copy(config.get("pipeline", {}))
    item_pair_builder_config = _mapping_copy(config.get("item_pair_builder", {}))

    return {
        "pipeline": {
            "session_timeout_minutes": pipeline_config.get("session_timeout_minutes"),
            "max_items_per_session": pipeline_config.get("max_items_per_session"),
        },
        "events": {
            "item_action_types": _normalized_action_types(config),
        },
        "item_pair_builder": item_pair_builder_config,
        "graph": {
            "distance_decay": {
                "enabled": False,
                "strategy": "none",
                "max_distance": None,
            },
            "time_decay": {
                "enabled": False,
                "strategy": "none",
            },
        },
    }


def validation_pair_semantics(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return the validation-specific semantics block for cache metadata."""
    validation_config = validation_ground_truth_config(config)
    return {
        "pipeline": _mapping_copy(validation_config.get("pipeline", {})),
        "events": _mapping_copy(validation_config.get("events", {})),
        "item_pair_builder": _mapping_copy(validation_config.get("item_pair_builder", {})),
        "graph": _mapping_copy(validation_config.get("graph", {})),
    }
