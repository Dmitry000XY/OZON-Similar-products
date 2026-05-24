# Дорожная карта проекта

1. EDA данных.
2. Очистка действий.
3. Сборка сессий.
4. Co-visitation baseline.
5. Нормализация похожести.
6. Business layer.
7. Формирование `sku | similar_items_sku_list`.
8. Offline evaluation.
9. Улучшения: query signals, Item2Vec, personalization, reranker.

## Бэклог дальнейшего рефакторинга (после cleanup)

- решить политику версии Python (`>=3.14` vs более широкий диапазон);
- возможный split retrieval на graph/scoring/ranking.
