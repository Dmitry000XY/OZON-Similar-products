# Ozon Similar Products Demo UI

Streamlit demo app for presenting recommendations produced by the offline
Ozon Similar Products pipeline. It is a presentation layer, not production
frontend code.

The app reads recommendation artifacts, lets you search a product by `item_id`
or product name, and shows similar products with rank, score, source, and a
human-readable source explanation.

## Install

```bash
uv sync
```

## Run Latest

By default, the app reads `outputs/latest/manifest.json`.

```bash
uv run streamlit run apps/demo/app.py
```

The same path can be passed explicitly:

```bash
uv run streamlit run apps/demo/app.py -- \
  --manifest-path outputs/latest/manifest.json
```

## Run A Specific Artifact

Open a concrete enriched recommendation parquet:

```bash
uv run streamlit run apps/demo/app.py -- \
  --enriched-path outputs/runs/<run_id>/recommendations/enriched.parquet
```

Open a detailed recommendation parquet:

```bash
uv run streamlit run apps/demo/app.py -- \
  --detailed-path outputs/runs/<run_id>/recommendations/detailed.parquet
```

Limit how many recommendations are shown for the selected product:

```bash
uv run streamlit run apps/demo/app.py -- \
  --manifest-path outputs/latest/manifest.json \
  --top-k 20
```

`--enriched-path` has priority over `--manifest-path`. If only a manifest is
provided, the app first tries `enriched.parquet`, then falls back to
`detailed.parquet`.

## Required Artifacts

Preferred input:

```text
outputs/runs/<run_id>/recommendations/enriched.parquet
```

Expected columns:

```text
item_id
item_name
similar_item_id
similar_item_name
rank
score
source
```

Fallback input:

```text
outputs/runs/<run_id>/recommendations/detailed.parquet
```

When `enriched.parquet` is missing, `detailed.parquet` still works, but product
names are shown as `None`.

## Tabs

- `Similar items`: search, random item, selected product card, similar products.
- `Run summary`: run metadata, artifact paths, row counts, and metrics when present.
- `Graph view`: embedded recommendation graph, Gephi HTML export, or graph build controls.
- `About`: short explanation of the pipeline signals and source labels.

## Graph Tab

The Graph tab looks for HTML in this order:

```text
outputs/runs/<run_id>/demo/gephi/index.html
outputs/runs/<run_id>/demo/graph/ego/item_id=<item_id>/ego_graph.html
outputs/runs/<run_id>/demo/graph/recommendations_graph.html
outputs/runs/<run_id>/demo/graph.html
apps/demo/assets/graph/index.html
```

`demo/gephi/index.html` has priority. If a polished Gephi/Sigma export exists,
the app shows it instead of the automatically generated HTML.

If graph HTML is missing, use the controls in the Graph tab and click
`Build graph`. Overview graphs are written to:

```text
outputs/runs/<run_id>/demo/graph/
  recommendations_graph.html
  recommendations_graph.json
  recommendations_graph.gexf
  manifest.json
```

Selected item graphs are built only on demand and are written to:

```text
outputs/runs/<run_id>/demo/graph/ego/item_id=<item_id>/
  ego_graph.html
  ego_graph.json
  ego_graph.gexf
  manifest.json
```

The app calls `ozon_similar_products.visualization.recommendation_graph`; it
does not duplicate graph-building logic inside Streamlit.

## Gephi Polish

1. Run `ozon-run-pipeline` or `ozon-run-full`.
2. Open `outputs/runs/<run_id>/demo/graph/recommendations_graph.gexf` in Gephi.
3. Use a readable layout such as ForceAtlas2, prevent overlap, node size by
   degree or `recommendation_count`, edge thickness by `score`, and edge color
   by `source_group`.
4. Export an interactive HTML view with a Gephi/Sigma plugin if available.
5. Put the export at `outputs/runs/<run_id>/demo/gephi/index.html`.
6. Reload the Graph tab; the demo will show the Gephi HTML first.

Gephi is optional. The automatically generated HTML/JSON/GEXF artifacts are
enough for the demo site to work.
