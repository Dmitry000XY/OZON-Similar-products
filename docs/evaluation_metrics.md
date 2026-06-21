# Evaluation metrics

Offline evaluation keeps two ground-truth views for the same validation data.

## Full ground truth

`full_ground_truth` contains every validation item pair that survived ground-truth
construction:

```text
item_id
relevant_item_id
relevance
target_action_type
evidence_count
view_count
click_count
favorite_count
to_cart_count
```

View-only pairs stay here intentionally. They are useful for diagnostics and for
checking how much recommendation traffic matches weak browsing evidence.

## Ranking ground truth

`ranking_ground_truth` is the filtered subset used by the primary ranking metrics.
By default it keeps pairs whose strongest validation action is one of:

```text
click
favorite
to_cart
```

This prevents accidental `view-only` matches from inflating ranking quality. For
example, if rank 1 is a view-only match and rank 2 is a click match, `mrr_at_k`
is `1 / 2`, not `1.0`.

The default evaluation config is:

```yaml
evaluation:
  relevance_mode: graded
  relevance_weights:
    view: 0.1
    click: 0.3
    favorite: 0.6
    to_cart: 1.0
  ranking_relevant_action_types:
    - click
    - favorite
    - to_cart
  min_ranking_relevance: 0.3
```

`binary` relevance is still supported, but the ranking action filter continues
to protect the primary metrics from view-only hits.

## Metric groups

These metrics are computed on `ranking_ground_truth`:

```text
hit_rate_at_k
recall_at_k
mrr_at_k
ndcg_at_k
coverage_at_k
fallback_hit_rate_at_k
fallback_recall_at_k
```

The explicit strong-action aliases use the same ranking ground truth:

```text
strong_hit_rate_at_k
strong_recall_at_k
strong_mrr_at_k
strong_ndcg_at_k
```

To-cart ranking quality is computed on pairs with `to_cart_count > 0`:

```text
to_cart_mrr_at_k
to_cart_ndcg_at_k
```

Action-specific hit/recall metrics remain diagnostics over `full_ground_truth`:

```text
view_hit_rate_at_k
view_recall_at_k
click_hit_rate_at_k
click_recall_at_k
favorite_hit_rate_at_k
favorite_recall_at_k
to_cart_hit_rate_at_k
to_cart_recall_at_k
```

The evaluation output also reports ground-truth sizes:

```text
ground_truth_pairs
all_evaluated_items
ranking_evaluated_items
view_only_ground_truth_pairs
ranking_ground_truth_pairs
```

`evaluated_items` is kept as a compatibility alias for
`ranking_evaluated_items`.
