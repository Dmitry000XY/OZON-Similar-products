# Demo site и recommendation graph

Streamlit demo site показывает итоговые рекомендации проекта Ozon Similar
Products: поиск товара, таблицу похожих товаров, сводку run-а и интерактивный
граф. По умолчанию сайт читает `outputs/latest/manifest.json`, но можно
передать `--manifest-path`, `--enriched-path` или `--detailed-path`.

Сайт намеренно привязан к уже построенным pipeline artifacts. Он не читает raw
data и не меняет scoring logic.

## Почему граф строится из recommendations

Граф строится из финальных recommendation rows, а не из всех pair statistics.
Pair stats могут быть очень большими и плохо объясняются на защите. Финальные
рекомендации уже отфильтрованы, ранжированы и совпадают с тем, что видно в
таблице:

```text
item_id -> similar_item_id
```

Так граф остаётся презентационным и не превращается в нечитаемую сеть.

## Узлы и рёбра

Каждый товар становится узлом. Основные поля узла:

```text
id
label
item_id
item_name
recommendation_count
in_degree
out_degree
degree
is_center
label_visible
x
y
```

Каждая рекомендация становится направленным ребром. Основные поля ребра:

```text
source
target
weight
score
rank
recommendation_source
source_group
color
```

Известные источники сохраняются как raw labels:

```text
behavioral
fallback_category_type_popular
fallback_category_popular
fallback_type_popular
fallback_brand_popular
fallback_global_popular
unknown
```

Fallback-рекомендации не являются ошибкой. Они подсвечиваются отдельно, чтобы
на защите было видно, где хватает behavioral-сигнала, а где pipeline подключает
популярные товары из категории, типа, бренда или глобального fallback.

## Режимы графа

Overview graph строится для run-а целиком. Production config по умолчанию:

```text
mode = overview
max_rank = 10
max_edges = 2000
max_nodes = null
labels_mode = important
theme = auto
include_behavioral = true
include_fallback = true
min_score = null
```

`max_nodes = null` означает “без ограничения числа узлов”. В CLI и UI то же
поведение обозначается как `All`. Для `max_edges` также можно указать `All`,
если нужно показать весь отфильтрованный граф.

Ego graph строится по требованию вокруг выбранного товара:

```text
center item
+ top N similar items
+ top M neighbors for each similar item
```

Streamlit создаёт ego-граф только после выбора товара и нажатия `Build graph`.
Это защищает demo run от множества лишних generated files.

## HTML-граф

Автоматический HTML export включает:

- zoom колесом мыши;
- pan перетаскиванием;
- reset view;
- поиск по `item_id` и названию;
- tooltip по узлу;
- подсветку соседей при hover;
- режимы подписей `Auto`, `Important`, `All`, `Off`;
- светлую, тёмную или auto-тему.

SVG занимает почти всю высоту вкладки, поэтому граф удобнее показывать на
проекторе. Подписи по умолчанию включаются только для важных узлов, чтобы
крупные overview-графы не превращались в облако текста.

## Artifacts

Production/full run сохраняет обзорный граф внутри run directory:

```text
outputs/runs/<run_id>/demo/graph/
  recommendations_graph.html
  recommendations_graph.json
  recommendations_graph.gexf
  manifest.json
```

Ego-граф из Streamlit сохраняется отдельно:

```text
outputs/runs/<run_id>/demo/graph/ego/item_id=<item_id>/
  ego_graph.html
  ego_graph.json
  ego_graph.gexf
  manifest.json
```

Tune trials не генерируют graph artifacts: tuning создаёт много trial
directories, и графы в них только добавили бы шум к objective selection.

## Gephi polish

Gephi остаётся опциональным ручным шагом для финальной презентации:

1. Откройте `outputs/runs/<run_id>/demo/graph/recommendations_graph.gexf`.
2. Примените ForceAtlas2 или похожий layout.
3. Включите prevent overlap.
4. Размер узлов задайте по `degree` или `recommendation_count`.
5. Толщину рёбер задайте по `score`.
6. Цвет рёбер задайте по `source_group`.
7. Экспортируйте интерактивный HTML через Gephi/Sigma plugin.
8. Положите результат в `outputs/runs/<run_id>/demo/gephi/index.html`.

Вкладка Graph сначала ищет `demo/gephi/index.html`, поэтому ручной polished
export автоматически перекрывает generated HTML. Если Gephi недоступен,
generated HTML/JSON/GEXF artifacts уже достаточны для demo site.

## Сценарий защиты

Рекомендуемый запуск:

```bash
uv run ozon-run-full 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml
uv run streamlit run apps/demo/app.py
```

Порядок показа:

1. переключить RU/EN при необходимости;
2. найти товар по `item_id` или названию;
3. показать карточку выбранного товара и таблицу похожих товаров;
4. объяснить behavioral и fallback источники;
5. открыть run summary и метрики;
6. показать overview graph;
7. построить ego graph выбранного товара;
8. при наличии открыть Gephi-polished graph.
