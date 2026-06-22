# Документация проекта

Эта папка хранит русскоязычную документацию по архитектуре MVP, контрактам данных и отдельным production-модулям
pipeline.

## Основные документы

- [Контракты данных](data_contract.md) — актуальные таблицы pipeline, обязательные колонки и границы ответственности
  слоёв.
- [Слой данных](modules/data_layer.md) — границы слоя данных и его публичный API.
- [Calibrated multi-channel веса](calibrated_multichannel_weights.md) — подробное решение по весам `view`, `click`,
  `favorite`, `to_cart`, частотной калибровке и тому, почему веса применяются только в `CoVisitationScorer`.
- [ItemPairBuilder](item_pair_builder.md) — как из сессий строятся directed item-item pairs, почему `signal_type` равен
  target action type, и почему приоритеты сигналов живут в config.
- [PairAggregator](aggregate_pairs.md) — как дневные пары агрегируются за rolling window и почему агрегатор считает
  только channel counts, без весов и score.
- [Graph scoring improvements](graph_scoring_improvements.md) — distance decay, time decay, weighted counts, weighted
  scoring и full graph/scoring tuning.
- [Evaluation metrics](evaluation_metrics.md) — разделение `full_ground_truth` и `ranking_ground_truth`, strong-action
  метрики и диагностика view-only пар.
- [Incremental update](incremental_update.md) — daily artifact manifests, idempotent pair stats writes и стратегия
  `pipeline.update_strategy`.
- [ItemPopularityBuilder](item_popularity_builder.md) — как считаются факты популярности товаров и статистика action
  type для будущей калибровки scorer-а.
- [Тюнинг и оценка fallback](fallback_tuning_evaluation.md) — fallback-метрики, пространство перебора параметров и
  логика целевой функции.
- [Работа с данными](data_io.md) — как распаковать архивы и читать parquet через data readers.
- [Архив EDA](archive/README.md) — где хранится legacy EDA-код и почему он не должен попадать в runtime.

## Главный принцип текущей архитектуры

До scoring-слоя мы не применяем веса:

```text
EventCleaner
→ SessionBuilder
→ ItemPairBuilder
→ PairAggregator
```

Эти слои сохраняют факты: `action_type`, `signal_type`, `view_count`, `click_count`, `favorite_count`, `to_cart_count`.

И только затем:

```text
CoVisitationScorer
```

применяет `business_weights`, inverse-frequency calibration и считает финальный `score`.
