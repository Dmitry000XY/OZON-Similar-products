# Scripts

Сервисные скрипты проекта.

- `prepare_raw_data.py` создает raw-папки, переносит настроенные архивы из корня проекта и безопасно распаковывает основные `tar.gz`.
- `check_project_structure.py` проверяет обязательную структуру проекта, наличие данных и отдельно показывает optional future слои.
- `run_pipeline.py` — thin wrapper над package CLI `ozon_similar_products.cli.run_pipeline`.
- `run_full.py` — full run: рекомендации + offline evaluation.
- `run_tune.py` — tuning по `configs/tuning/search_space.yaml` (`grid`, `random`, `successive_halving`, `simulated_annealing`).
- `compare_tuning.py` — просмотр `outputs/tuning/*/results.csv`.
- `preview_latest_recommendations.py` — thin wrapper над package CLI `ozon_similar_products.cli.preview_recommendations`.

Fallback-метрики, параметры тюнинга и целевая функция описаны в
`docs/fallback_tuning_evaluation.md`.
