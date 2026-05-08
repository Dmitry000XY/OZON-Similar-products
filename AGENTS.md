# AGENTS.md

This file is the working guide for AI agents and contributors working in this repository.

It applies to the whole repository unless a more specific `AGENTS.md` is added inside a subdirectory.

---

## 1. Project context

Project: `ozon-similar-products`

Goal: build an offline pipeline for the Ozon Fresh "similar products" widget.

The product-level task is:

```text
item_id / sku -> similar_items_sku_list
```

Inside the codebase we use `item_id`. In the final case description, `item_id` should be explained as the local equivalent of `sku`.

The MVP is **offline item-to-item retrieval** based on user behavior. The first baseline is a **co-visitation graph**: two products are considered similar if users often interact with them in the same context, primarily within the same session.

Do not turn the MVP into a one-off notebook. The project must look like a reproducible offline pipeline.

---

## 2. Current repository shape

The current repository is organized around this structure:

```text
configs/
  baseline.yaml
  evaluation.yaml
  paths.yaml

data/
  raw/
  interim/
  processed/
  samples/

docs/
  architecture.md
  project_roadmap.md

notebooks/

outputs/
  figures/
  recommendations/
  reports/

scripts/

src/ozon_similar_products/
  config.py
  paths.py

  data/
    archives.py
    config.py
    partitions.py
    readers.py
    schemas.py
    validation.py

  preprocessing/
  features/
  retrieval/
  business/
  evaluation/
  output/

tests/
```

Expected MVP additions:

```text
src/ozon_similar_products/
  preprocessing/
    clean_events.py
    build_sessions.py

  features/
    item_popularity.py

  retrieval/
    build_pairs.py
    aggregate_pairs.py
    update_strategy.py
    scoring.py
    topk.py

  output/
    writers.py
    lookup.py

  pipeline/
    run_mvp.py
```

If these files already exist, modify them instead of creating duplicates.

---

## 3. Tech stack and commands

Use the project tooling already declared in `pyproject.toml`.

Runtime dependencies:

```text
polars
pyyaml
```

Dev dependency:

```text
pytest
```

Python version currently declared by the project:

```text
>=3.14
```

Common commands:

```powershell
uv sync
uv run pytest
```

If using dependency groups:

```powershell
uv sync --group dev
uv run pytest
```

Do not introduce new dependencies unless they are clearly needed and discussed.

Prefer:

```python
import polars as pl
from pathlib import Path
```

Avoid using pandas for MVP pipeline code unless explicitly approved.

---

## 4. Git and repository hygiene

Never commit:

```text
.venv/
.idea/
__pycache__/
*.egg-info/
data/raw/
data/interim/
data/processed/
data/samples/
outputs/
*.parquet
*.csv
```

Data and generated artifacts should stay local unless the task explicitly requires committing a small synthetic fixture.

Keep commits focused. Good branch names:

```text
feature/mvp-interfaces
feature/clean-events
feature/build-sessions
feature/build-pairs
feature/scoring-topk
feature/output-lookup
feature/run-mvp
```

---

## 5. MVP scope

The MVP minimum is:

```text
1. EDA and data loading produce agreed inputs.
2. Clean raw events.
3. Build user sessions.
4. Build item-item co-visitation pairs.
5. Aggregate pair statistics.
6. Score item pairs.
7. Select top-K similar items per item.
8. Save recommendations.
9. Provide get_similar_items(item_id).
```

EDA and data loading are treated as upstream work. Do not block interface implementation on them. MVP modules should be testable on synthetic `polars.DataFrame` objects.

---

## 6. Upstream requirements from EDA and data loading

The MVP pipeline assumes that the data loading layer can provide daily or windowed data in the expected format.

Expected raw `user_actions` columns:

```text
user_id
date
timestamp
action_type
widget_name
search_query
item_id
```

Expected `product_information` columns:

```text
item_id
name
brand
type
category_id
category_name
```

EDA must eventually clarify:

```text
- actual action_type values;
- which action types contain item_id;
- which actions go into the item-to-item graph;
- how search events should be handled;
- timestamp format;
- date range;
- whether daily partitions exist;
- anomalously active users;
- recommended session timeout;
- max_items_per_session.
```

Until EDA is complete, use the following MVP assumptions:

```text
item_action_types = ["view", "click", "favorite", "to_cart"]
search_action_type = "search"
session_timeout_minutes = 30
max_items_per_session = 50
top_k = 20
```

---

## 7. Large data rule

Assume the full dataset may not fit into memory.

Design all MVP modules so they can work with daily partitions and/or `pl.LazyFrame`.

Preferred processing model:

```text
for each event_date:
    raw events -> clean events -> sessions -> daily item pairs

then:
    aggregate daily pairs over rolling window
    score pairs
    select top-K
    save versioned recommendations
```

Do not force full raw data into memory unless working with a sample or synthetic test data.

Allowed:

```python
pl.scan_parquet(...)
pl.LazyFrame
daily partitions
windowed aggregation
```

Be careful with:

```python
.collect()
pl.read_parquet(...)
```

Use eager operations only when the input is known to be small or is already a per-day partition.

---

## 8. Target MVP pipeline

The MVP pipeline should follow this sequence:

```text
prepared daily events
    ↓
EventCleaner
    ↓
SessionBuilder
    ↓
ItemPopularityBuilder
    ↓
ItemPairBuilder
    ↓
PairAggregator
    ↓
CoVisitationScorer
    ↓
TopKSelector
    ↓
RecommendationWriter
    ↓
SimilarItemsLookup
```

The daily pipeline architecture must remain compatible with:

```text
daily full retrain over rolling window
future incremental update
versioned recommendations
latest snapshot / latest manifest
```

---

## 9. Data contracts

The canonical place for DataFrame column contracts is:

```text
src/ozon_similar_products/data/schemas.py
```

The canonical place for validation helpers is:

```text
src/ozon_similar_products/data/validation.py
```

Do not create a separate `contracts/` package unless the team explicitly decides to refactor.

### 9.1 Raw events

```python
RAW_EVENTS_COLUMNS = [
    "user_id",
    "date",
    "timestamp",
    "action_type",
    "widget_name",
    "search_query",
    "item_id",
]
```

### 9.2 Product information

```python
PRODUCT_INFORMATION_COLUMNS = [
    "item_id",
    "name",
    "brand",
    "type",
    "category_id",
    "category_name",
]
```

### 9.3 Clean events

Output of `EventCleaner`.

```python
CLEAN_EVENTS_COLUMNS = [
    "user_id",
    "event_date",
    "timestamp",
    "action_type",
    "item_id",
    "search_query",
    "widget_name",
    "action_weight",
]
```

### 9.4 Sessions

Output of `SessionBuilder`.

```python
SESSIONS_COLUMNS = [
    "user_id",
    "session_id",
    "event_date",
    "timestamp",
    "action_type",
    "item_id",
    "action_weight",
]
```

### 9.5 Item popularity

Output of `ItemPopularityBuilder`.

```python
ITEM_POPULARITY_COLUMNS = [
    "item_id",
    "events_count",
    "unique_users",
    "views_count",
    "clicks_count",
    "favorites_count",
    "to_cart_count",
]
```

### 9.6 Daily item pairs

Output of `ItemPairBuilder`.

```python
DAILY_ITEM_PAIRS_COLUMNS = [
    "pair_date",
    "item_id",
    "similar_item_id",
    "session_id",
    "user_id",
    "pair_weight",
]
```

### 9.7 Pair aggregates

Output of `PairAggregator`.

```python
PAIR_AGGREGATES_COLUMNS = [
    "item_id",
    "similar_item_id",
    "pair_count",
    "weight_sum",
    "unique_users",
    "unique_sessions",
    "window_start",
    "window_end",
]
```

### 9.8 Pair scores

Output of `CoVisitationScorer`.

```python
PAIR_SCORES_COLUMNS = [
    "item_id",
    "similar_item_id",
    "score",
    "pair_count",
    "weight_sum",
    "unique_users",
    "unique_sessions",
]
```

### 9.9 Detailed recommendations

Output of `TopKSelector`.

```python
RECOMMENDATIONS_COLUMNS = [
    "item_id",
    "similar_item_id",
    "score",
    "rank",
    "source",
]
```

For MVP:

```text
source = "behavioral"
```

### 9.10 Widget output

Final widget format.

```python
WIDGET_OUTPUT_COLUMNS = [
    "item_id",
    "similar_items_sku_list",
]
```

---

## 10. Validation rules

Every module should validate its input and output using helpers from:

```text
src/ozon_similar_products/data/validation.py
```

Expected helper functions:

```python
validate_columns(actual_columns, expected_columns)
validate_frame_has_columns(frame, expected_columns)

validate_raw_events(frame)
validate_product_information(frame)
validate_clean_events(frame)
validate_sessions(frame)
validate_item_popularity(frame)
validate_daily_item_pairs(frame)
validate_pair_aggregates(frame)
validate_pair_scores(frame)
validate_recommendations(frame)
validate_widget_output(frame)
```

Validation should check required columns. It does not need to enforce strict dtypes during the first MVP interface pass, but dtype checks can be added later.

---

## 11. Module responsibilities and interfaces

### 11.1 `data/readers.py`

Responsibility:

```text
read parquet data
support file or directory input
validate required raw columns
return Polars DataFrame or LazyFrame
```

Do not clean events here.

Do not build sessions here.

Do not build item pairs here.

Known issue to check: if `load_data_config()` reads `configs/data.yaml`, ensure that file exists or refactor the reader to use the current config files.

---

### 11.2 `preprocessing/clean_events.py`

Class:

```python
class EventCleaner:
    def __init__(
        self,
        item_action_types: list[str],
        action_weights: dict[str, float],
    ) -> None:
        ...

    def transform_day(self, events: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
        ...

    def transform_window(
        self,
        daily_events: list[pl.DataFrame | pl.LazyFrame],
    ) -> pl.DataFrame:
        ...
```

Responsibility:

```text
parse timestamp
create event_date
drop duplicates
keep item actions with item_id
separate search without item_id from graph events
add action_weight
return clean_events
```

MVP behavior:

```text
view / click / favorite / to_cart -> graph events
search -> not used for co-visitation MVP
```

Do not build sessions here.

---

### 11.3 `preprocessing/build_sessions.py`

Class:

```python
class SessionBuilder:
    def __init__(
        self,
        timeout_minutes: int = 30,
        max_items_per_session: int = 50,
    ) -> None:
        ...

    def transform_day(self, events_clean: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
        ...

    def transform_window(
        self,
        daily_clean_events: list[pl.DataFrame | pl.LazyFrame],
    ) -> pl.DataFrame:
        ...
```

Responsibility:

```text
sort events by user_id and timestamp
compute time gaps
create session_id
limit too-long sessions
return sessions
```

MVP simplification:

```text
Sessions may be built inside each day.
Cross-day session carry-over is a future improvement.
```

---

### 11.4 `features/item_popularity.py`

Class:

```python
class ItemPopularityBuilder:
    def transform_day(self, events_clean: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
        ...

    def aggregate_window(
        self,
        daily_popularity: list[pl.DataFrame | pl.LazyFrame],
    ) -> pl.DataFrame:
        ...
```

Responsibility:

```text
count events per item
count unique users per item
count views/clicks/favorites/to_cart
produce item popularity features
```

MVP can use this artifact mainly for diagnostics. Strong baseline will use it for popularity normalization and fallback.

---

### 11.5 `retrieval/build_pairs.py`

Class:

```python
class ItemPairBuilder:
    def __init__(self, max_items_per_session: int = 50) -> None:
        ...

    def transform_day(self, sessions: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
        ...
```

Responsibility:

```text
build item-item pairs inside session_id
produce directed pairs A -> B and B -> A
remove self-pairs
limit contribution from too-long sessions
compute pair_weight
return daily item pairs
```

Why directed pairs:

```text
They make top-K recommendations per item_id straightforward.
```

---

### 11.6 `retrieval/aggregate_pairs.py`

Class:

```python
class PairAggregator:
    def aggregate_window(
        self,
        daily_pairs: list[pl.DataFrame | pl.LazyFrame],
        window_start: str,
        window_end: str,
    ) -> pl.DataFrame:
        ...
```

Responsibility:

```text
aggregate daily item pairs over rolling window
compute pair_count
compute weight_sum
compute unique_users
compute unique_sessions
add window_start and window_end
```

This layer is required even for MVP because it prepares the architecture for future incremental update.

---

### 11.7 `retrieval/update_strategy.py`

Protocol:

```python
class GraphUpdateStrategy(Protocol):
    def update(
        self,
        train_until_date: str,
        lookback_days: int,
    ) -> pl.DataFrame:
        ...
```

MVP implementation:

```python
class FullRetrainStrategy:
    def update(
        self,
        train_until_date: str,
        lookback_days: int,
    ) -> pl.DataFrame:
        ...
```

Future implementation:

```python
class IncrementalUpdateStrategy:
    def update(
        self,
        train_until_date: str,
        lookback_days: int,
    ) -> pl.DataFrame:
        ...
```

Responsibility:

```text
hide the graph update method from the rest of the pipeline
allow replacing full retrain with incremental update later
```

---

### 11.8 `retrieval/scoring.py`

Class:

```python
class CoVisitationScorer:
    def __init__(self, method: str = "pair_count") -> None:
        ...

    def score(self, pair_aggregates: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
        ...
```

MVP scoring methods:

```text
pair_count
weight_sum
```

Future scoring methods:

```text
cosine_normalized
lift
pmi_like
hybrid
```

Do not select top-K here.

---

### 11.9 `retrieval/topk.py`

Class:

```python
class TopKSelector:
    def __init__(self, top_k: int = 20) -> None:
        ...

    def select(self, pair_scores: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
        ...
```

Responsibility:

```text
sort candidates per item_id
assign rank
keep top-K
add source = "behavioral"
```

Stable sorting rule:

```text
item_id ASC
score DESC
similar_item_id ASC
```

---

### 11.10 `output/writers.py`

Class:

```python
class RecommendationWriter:
    def save_detailed(
        self,
        recommendations: pl.DataFrame,
        output_path: str | Path,
    ) -> None:
        ...

    def save_widget_format(
        self,
        recommendations: pl.DataFrame,
        output_path: str | Path,
    ) -> None:
        ...
```

Responsibility:

```text
save detailed recommendation table
save widget format table
create parent directories if needed
```

Do not compute recommendations here.

---

### 11.11 `output/lookup.py`

Class:

```python
class SimilarItemsLookup:
    def __init__(self, recommendations_path: str | Path) -> None:
        ...

    def get_similar_items(
        self,
        item_id: int | str,
        top_k: int = 10,
    ) -> list[int | str]:
        ...
```

Responsibility:

```text
read saved recommendations
return similar item ids
```

Do not recompute the graph.

---

### 11.12 `pipeline/run_mvp.py`

Function:

```python
def run_mvp_pipeline(
    train_until_date: str,
    lookback_days: int,
    config_path: str | Path = "configs/baseline.yaml",
) -> None:
    ...
```

Pipeline stages:

```text
1. obtain daily raw events from data loading layer
2. clean events by day
3. build sessions by day
4. build daily item pairs
5. aggregate pairs over rolling window
6. score pairs
7. select top-K
8. save detailed recommendations
9. save widget output
10. update latest snapshot
```

The pipeline should depend on interfaces, not hidden notebook logic.

---

## 12. Config expectations

Main MVP config:

```text
configs/baseline.yaml
```

Recommended fields:

```yaml
pipeline:
  session_timeout_minutes: 30
  max_items_per_session: 50
  top_k: 20
  lookback_days: 30

events:
  item_action_types:
    - view
    - click
    - favorite
    - to_cart
  search_action_type: search

scoring:
  method: pair_count
  action_weights:
    view: 1.0
    click: 2.0
    favorite: 2.5
    to_cart: 4.0

artifacts:
  events_clean_dir: data/processed/events_clean
  sessions_dir: data/processed/sessions
  item_popularity_dir: data/processed/item_popularity
  daily_pairs_dir: data/processed/item_pairs
  pair_aggregates_dir: data/processed/pair_aggregates

outputs:
  detailed_recommendations_dir: outputs/recommendations/detailed
  widget_recommendations_dir: outputs/recommendations/widget
  latest_dir: outputs/recommendations/latest
```

---

## 13. Testing rules

All MVP modules must be testable on small synthetic `polars.DataFrame` inputs.

Do not require real raw data in unit tests.

Expected tests:

```text
tests/test_clean_events.py
tests/test_build_sessions.py
tests/test_item_popularity.py
tests/test_build_pairs.py
tests/test_aggregate_pairs.py
tests/test_update_strategy.py
tests/test_scoring.py
tests/test_topk.py
tests/test_writers.py
tests/test_lookup.py
tests/test_run_mvp.py
```

Each test should verify:

```text
input contract
output contract
basic behavior
edge cases
```

Examples of edge cases:

```text
empty frame
missing required columns
single-item session
self-pairs
ties in score
item with fewer than top_k candidates
```

---

## 14. Parallel task split

### Task A — data contracts and validation

Files:

```text
src/ozon_similar_products/data/schemas.py
src/ozon_similar_products/data/validation.py
tests/test_schemas.py
```

### Task B — event cleaning

Files:

```text
src/ozon_similar_products/preprocessing/clean_events.py
tests/test_clean_events.py
```

### Task C — session building

Files:

```text
src/ozon_similar_products/preprocessing/build_sessions.py
tests/test_build_sessions.py
```

### Task D — item popularity

Files:

```text
src/ozon_similar_products/features/item_popularity.py
tests/test_item_popularity.py
```

### Task E — item pair building

Files:

```text
src/ozon_similar_products/retrieval/build_pairs.py
tests/test_build_pairs.py
```

### Task F — pair aggregation and update strategy

Files:

```text
src/ozon_similar_products/retrieval/aggregate_pairs.py
src/ozon_similar_products/retrieval/update_strategy.py
tests/test_aggregate_pairs.py
tests/test_update_strategy.py
```

### Task G — scoring and top-K

Files:

```text
src/ozon_similar_products/retrieval/scoring.py
src/ozon_similar_products/retrieval/topk.py
tests/test_scoring.py
tests/test_topk.py
```

### Task H — output and lookup

Files:

```text
src/ozon_similar_products/output/writers.py
src/ozon_similar_products/output/lookup.py
tests/test_writers.py
tests/test_lookup.py
```

### Task I — pipeline integration

Files:

```text
src/ozon_similar_products/pipeline/run_mvp.py
tests/test_run_mvp.py
```

---

## 15. Implementation order

Recommended order:

```text
1. schemas.py + validation.py
2. interface files and import smoke tests
3. EventCleaner
4. SessionBuilder
5. ItemPairBuilder
6. PairAggregator
7. CoVisitationScorer
8. TopKSelector
9. RecommendationWriter + SimilarItemsLookup
10. run_mvp_pipeline
```

Do not start with a large all-in-one pipeline implementation.

---

## 16. Future compatibility rules

When implementing MVP, keep these future stages in mind:

```text
business rules
fallback
offline evaluation
co-search
time decay
Item2Vec
hybrid retrieval
reranker
incremental update
online serving
```

MVP code must not make these future stages impossible.

Therefore:

```text
- keep PairBuilder separate from PairAggregator;
- keep Scorer separate from TopKSelector;
- keep Writer/Lookup separate from model code;
- keep GraphUpdateStrategy separate from scoring;
- preserve source column in recommendations;
- preserve daily item pairs if possible.
```

---

## 17. Do not do this

Do not:

```text
- write one huge run.py with all logic mixed together;
- make modules depend on notebooks;
- read the entire raw dataset in every unit test;
- commit data files;
- commit outputs;
- implement reranker before baseline;
- mix search_query into co-visitation score in MVP;
- hardcode paths inside business logic;
- silently ignore missing required columns;
- return DataFrames with undocumented columns only.
```

---

## 18. Definition of done for MVP interfaces

The MVP interface task is done when:

```text
- all data contracts exist in data/schemas.py;
- validation helpers exist in data/validation.py;
- all MVP module files exist;
- all MVP classes/functions are importable;
- baseline.yaml contains MVP config fields;
- import smoke tests pass;
- each block has a clear input and output contract;
- tasks can be distributed between team members.
```

Run:

```powershell
uv run pytest
```

before committing.
