# Demo UI для похожих товаров Ozon

Streamlit-приложение показывает результаты offline pipeline в формате,
удобном для защиты и ручного просмотра. Это презентационный слой: он читает
готовые artifacts run-а и не меняет алгоритм рекомендаций.

## Запуск

По умолчанию приложение читает `outputs/latest/manifest.json`:

```bash
uv run streamlit run apps/demo/app.py
```

Явно указать manifest:

```bash
uv run streamlit run apps/demo/app.py -- \
  --manifest-path outputs/latest/manifest.json
```

Открыть конкретный parquet с enriched-рекомендациями:

```bash
uv run streamlit run apps/demo/app.py -- \
  --enriched-path outputs/runs/<run_id>/recommendations/enriched.parquet
```

Открыть detailed parquet, если enriched-файл не был собран:

```bash
uv run streamlit run apps/demo/app.py -- \
  --detailed-path outputs/runs/<run_id>/recommendations/detailed.parquet
```

Ограничить число рекомендаций в таблице выбранного товара:

```bash
uv run streamlit run apps/demo/app.py -- \
  --manifest-path outputs/latest/manifest.json \
  --top-k 20
```

Приоритет источников такой: `--enriched-path`, затем `--manifest-path`, затем
`--detailed-path`.

## Интерфейс

В правом верхнем углу есть переключатель языка `EN/RU`. По умолчанию выбран
английский язык, чтобы демо сразу выглядело нейтрально для защиты; русский
перевод покрывает вкладки, подписи таблиц, подсказки и объяснения источников.

Основные вкладки:

- `Similar items`: поиск по `item_id` или названию, случайный товар, карточка
  выбранного товара и таблица похожих товаров.
- `Run summary`: параметры run-а, пути к artifacts, число строк по этапам и
  demo/evaluation metrics.
- `Graph view`: обзорный граф рекомендаций, ego-граф выбранного товара,
  фильтры источников, лимиты, режим подписей и тема.
- `About`: короткое объяснение behavioral и fallback сигналов.

Карточка выбранного товара показывает название крупно, а `item_id` вторичным
текстом. Таблица рекомендаций использует человекочитаемые названия колонок и
объясняет источник каждой рекомендации.

## Входные данные

Лучший вариант для демо:

```text
outputs/runs/<run_id>/recommendations/enriched.parquet
```

Ожидаемые колонки:

```text
item_id
item_name
similar_item_id
similar_item_name
rank
score
source
```

Если `enriched.parquet` отсутствует, можно открыть `detailed.parquet`. В этом
режиме рекомендации будут работать, но названия товаров могут быть пустыми.

## Граф

Вкладка `Graph view` ищет готовый HTML в таком порядке:

```text
outputs/runs/<run_id>/demo/gephi/index.html
outputs/runs/<run_id>/demo/graph/ego/item_id=<item_id>/ego_graph.html
outputs/runs/<run_id>/demo/graph/recommendations_graph.html
outputs/runs/<run_id>/demo/graph.html
apps/demo/assets/graph/index.html
```

Если существует polished Gephi/Sigma export, приложение покажет его первым.
Если HTML нет, нажмите `Build graph`: Streamlit вызовет общий exporter из
`ozon_similar_products.visualization.recommendation_graph`.

Обзорный граф сохраняется здесь:

```text
outputs/runs/<run_id>/demo/graph/
  recommendations_graph.html
  recommendations_graph.json
  recommendations_graph.gexf
  manifest.json
```

Ego-граф выбранного товара создаётся по требованию:

```text
outputs/runs/<run_id>/demo/graph/ego/item_id=<item_id>/
  ego_graph.html
  ego_graph.json
  ego_graph.gexf
  manifest.json
```

В HTML-графе доступны zoom колесом, pan мышью, reset view, поиск, tooltip по
узлу, подсветка соседей и переключение подписей. Значение `All` для
`max_edges` или `max_nodes` означает отсутствие соответствующего лимита; на
больших графах браузер может работать медленнее.

## Gephi

Gephi не обязателен, но полезен для финальной ручной доводки:

1. Запустите production/full pipeline.
2. Откройте `outputs/runs/<run_id>/demo/graph/recommendations_graph.gexf`.
3. Примените ForceAtlas2 или похожий layout, включите prevent overlap.
4. Размер узлов задайте по `degree` или `recommendation_count`.
5. Толщину рёбер задайте по `score`, цвет рёбер по `source_group`.
6. Экспортируйте интерактивный HTML через Gephi/Sigma plugin.
7. Положите export в `outputs/runs/<run_id>/demo/gephi/index.html`.
8. Обновите вкладку `Graph view`.

Автоматические HTML/JSON/GEXF artifacts достаточны для работы демо без Gephi.
