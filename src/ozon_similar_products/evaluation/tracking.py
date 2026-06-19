"""Experiment tracking helpers for offline evaluation."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from ozon_similar_products.evaluation.metrics import OfflineMetrics
from ozon_similar_products.output.manifest import json_ready


def _json_ready_dataclass(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _json_ready_dataclass(item) for key, item in asdict(value).items()}
    return json_ready(value)


def write_json(path: str | Path, payload: Mapping[str, Any]) -> Path:
    """Write a JSON payload with stable formatting."""

    resolved_path = Path(path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(
        json.dumps(
            _json_ready_dataclass(dict(payload)),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return resolved_path


def metrics_to_flat_dict(metrics: OfflineMetrics) -> dict[str, Any]:
    """Convert metrics dataclass to a flat dictionary."""

    return asdict(metrics)


def append_experiment_index(
    index_path: str | Path,
    row: Mapping[str, Any],
) -> Path:
    """Append one experiment row to a CSV index."""

    resolved_path = Path(index_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    normalized_row = {key: _json_ready_dataclass(value) for key, value in row.items()}

    fieldnames = list(normalized_row.keys())
    file_exists = resolved_path.exists()

    with resolved_path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(normalized_row)

    return resolved_path
