# Architecture

Проект строится как offline pipeline похожих товаров для Ozon Fresh.

## Data

- `data/raw/archives/` хранит исходные архивы.
- `data/raw/product_information/` хранит справочник товаров.
- `data/raw/user_actions/` хранит parquet-партиции пользовательских действий.
- `data/interim/` предназначена для очищенных действий, сессий и пар товаров.
- `data/processed/` предназначена для таблиц, готовых к моделям или дальнейшей выдаче.
- `data/samples/` предназначена для маленьких sample-файлов.

## Code

- `src/ozon_similar_products/data/` - загрузчики, схемы и проверки данных.
- `src/ozon_similar_products/preprocessing/` - очистка действий, товаров и сборка сессий.
- `src/ozon_similar_products/features/` - веса событий, пары товаров и query signals.
- `src/ozon_similar_products/retrieval/` - co-visitation baseline, нормализация похожести и top-k.
- `src/ozon_similar_products/business/` - бизнес-фильтры, дедупликация и fallback-правила.
- `src/ozon_similar_products/evaluation/` - сплиты, метрики и анализ срезов.
- `src/ozon_similar_products/output/` - приведение результата к формату `sku | similar_items_sku_list`.

TODO: подробно описать последовательность будущего offline pipeline после первого EDA: raw -> clean events -> sessions -> item pairs -> retrieval -> business filters -> output.

## Outputs

- `outputs/recommendations/` - финальные рекомендации.
- `outputs/reports/` - отчеты EDA и метрик.
- `outputs/figures/` - графики и изображения анализа.
