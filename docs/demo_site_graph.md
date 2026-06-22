# Demo site and recommendation graph

Streamlit demo site is a small presentation layer for the case "similar products by user interests". It reads run
artifacts from `outputs/latest/manifest.json` by default, or from explicit `--manifest-path`, `--enriched-path`, or
`--detailed-path` arguments.

The site is intentionally tied to already produced pipeline outputs. It does not change recommendation logic and does
not read raw data.

## Why the graph uses recommendations

The demo graph is built from final recommendations, not from all pair statistics. Pair stats can be very large and are
hard to explain visually. Recommendation rows are smaller, ranked, and directly match what users see in the widget:

```text
item_id -> similar_item_id
```

This keeps the graph presentation-focused and avoids rendering a huge unreadable network.

## Nodes and edges

Each item is a node. Node fields include:

```text
id
label
item_id
item_name
recommendation_count
in_degree
out_degree
degree
```

Each recommendation is a directed edge. Edge fields include:

```text
source
target
weight
score
rank
recommendation_source
source_group
```

Known source labels are preserved:

```text
behavioral
fallback_category_type_popular
fallback_category_popular
fallback_type_popular
fallback_brand_popular
fallback_global_popular
unknown
```

Fallback recommendations are not errors. They are grouped and colored separately so the presenter can explain where
behavioral coverage ends and business fallback begins.

## Graph modes

Overview graph is generated for a run. Default limits:

```text
max_rank = 10
max_edges = 2000
max_nodes = 500
include_fallback = true
prefer_behavioral = true
min_score = null
```

The exporter keeps higher-quality edges first: behavioral edges, higher score, then lower rank. If the node limit is
exceeded, it keeps high-degree source nodes first and drops dangling edges.

Selected item graph is an on-demand ego graph:

```text
center item
+ top N similar items
+ top M neighbors for each similar item
```

The Streamlit app builds ego graphs only after a user selects an item and clicks `Build graph`. It does not generate an
ego graph for every item.

## Artifacts

Normal production/full runs can generate overview graph artifacts under the run directory:

```text
outputs/runs/<run_id>/demo/graph/
  recommendations_graph.html
  recommendations_graph.json
  recommendations_graph.gexf
  manifest.json
```

Selected item graphs created from Streamlit are written to:

```text
outputs/runs/<run_id>/demo/graph/ego/item_id=<item_id>/
  ego_graph.html
  ego_graph.json
  ego_graph.gexf
  manifest.json
```

Tune trials do not generate graph artifacts. Tuning writes many trial directories, and graph export would add noise and
heavy generated files that are not useful for objective selection.

## Automatic and manual steps

Automatic:

1. `configs/production.yaml` enables `demo.graph`.
2. `run_pipeline` exports graph artifacts after recommendation files are written.
3. `run_full` preserves the same run artifacts and adds evaluation metadata.
4. The run manifest includes `demo.graph` paths and node/edge counts.
5. Streamlit embeds the graph HTML when it exists.

Manual:

1. Open `outputs/runs/<run_id>/demo/graph/recommendations_graph.gexf` in Gephi.
2. Apply layout such as ForceAtlas2.
3. Enable prevent overlap.
4. Size nodes by `degree` or `recommendation_count`.
5. Size edges by `score`.
6. Color edges by `source_group`.
7. Export interactive HTML with a Gephi/Sigma plugin if available.
8. Put it at `outputs/runs/<run_id>/demo/gephi/index.html`.

The Streamlit Graph tab checks `demo/gephi/index.html` first, so a polished manual export overrides the automatic HTML.
Gephi remains optional: the site works with the generated HTML/JSON/GEXF artifacts.

## Demo defense flow

Recommended flow for a defense run:

```bash
uv run ozon-run-full 2024-04-23 --lookback-days 1 --validation-days 1 --top-k 20 --config-path configs/production.yaml
uv run streamlit run apps/demo/app.py
```

Then use the site to show:

1. product search;
2. similar products for a selected item;
3. behavioral and fallback source labels;
4. run summary and metrics;
5. overview graph;
6. selected item neighborhood graph;
7. optional Gephi-polished graph.
