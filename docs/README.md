# Документация проекта

Эта папка содержит документацию по проекту **OZON Similar Products**.

Главный README в корне репозитория даёт общее представление о проекте, показывает быстрый запуск и демонстрирует
результат. Этот файл нужен как карта документации: он помогает быстро найти подробности по архитектуре, данным, запуску,
оценке качества и отдельным модулям.

## С чего начать

Если вы впервые открыли проект, лучше читать документы в таком порядке:

1. [`../README.md`](../README.md) — общее описание проекта, демонстрация результата и быстрый запуск.
2. [`../scripts/README.md`](../scripts/README.md) — основные команды запуска.
3. [`architecture.md`](architecture.md) — общий путь данных и архитектурные решения.
4. [`data_contract.md`](data_contract.md) — таблицы, колонки и границы ответственности между слоями.
5. README внутри нужного модуля в [`../src/ozon_similar_products/`](../src/ozon_similar_products/).

## Быстрая навигация

| Задача                             | Куда идти                                        |
|------------------------------------|--------------------------------------------------|
| Понять проект целиком              | [`../README.md`](../README.md)                   |
| Запустить проект                   | [`../scripts/README.md`](../scripts/README.md)   |
| Настроить конфиги                  | [`../configs/README.md`](../configs/README.md)   |
| Понять архитектуру                 | [`architecture.md`](architecture.md)             |
| Разобраться с таблицами            | [`data_contract.md`](data_contract.md)           |
| Понять метрики качества            | [`evaluation_metrics.md`](evaluation_metrics.md) |
| Понять incremental-режим           | [`incremental_update.md`](incremental_update.md) |
| Настроить локальный тяжёлый запуск | [`local_runner.md`](local_runner.md)             |

## Основные документы

| Документ                                         | Что объясняет                                                                                             |
|--------------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| [`architecture.md`](architecture.md)             | как устроен проект и как данные проходят путь от сырых событий до рекомендаций                            |
| [`data_contract.md`](data_contract.md)           | какие таблицы используются, какие поля в них ожидаются и где появляются `score`, `rank` и итоговый lookup |
| [`evaluation_metrics.md`](evaluation_metrics.md) | какие offline-метрики считаются и как отличать основные метрики от диагностических                        |
| [`incremental_update.md`](incremental_update.md) | как переиспользуются дневные артефакты и как устроена защита от некорректного reuse                       |
| [`local_runner.md`](local_runner.md)             | как запускать тяжёлые сценарии через локальный self-hosted GitHub Actions runner                          |

## README по модулям

Подробности по конкретным частям кода хранятся рядом с модулями. Если меняется модуль, его документацию лучше обновлять
там же.

| Раздел                                      | Документ                                                                          |
|---------------------------------------------|-----------------------------------------------------------------------------------|
| Чтение данных, схемы и валидация            | [`data/README.md`](../src/ozon_similar_products/data/README.md)                   |
| Очистка событий и построение сессий         | [`preprocessing/README.md`](../src/ozon_similar_products/preprocessing/README.md) |
| Популярность товаров и служебные статистики | [`features/README.md`](../src/ozon_similar_products/features/README.md)           |
| Пары товаров, агрегация, scoring и top-K    | [`retrieval/README.md`](../src/ozon_similar_products/retrieval/README.md)         |
| Резервные рекомендации и бизнес-правила     | [`business/README.md`](../src/ozon_similar_products/business/README.md)           |
| Проверка качества рекомендаций              | [`evaluation/README.md`](../src/ozon_similar_products/evaluation/README.md)       |
| Полный запуск конвейера                     | [`pipeline/README.md`](../src/ozon_similar_products/pipeline/README.md)           |
| Сохранение результатов                      | [`output/README.md`](../src/ozon_similar_products/output/README.md)               |
| Чтение готового lookup-результата           | [`serving/README.md`](../src/ozon_similar_products/serving/README.md)             |
| Диагностика данных и результатов            | [`diagnostics/README.md`](../src/ozon_similar_products/diagnostics/README.md)     |

## Служебные документы

| Раздел                     | Документ                                           |
|----------------------------|----------------------------------------------------|
| Команды запуска            | [`../scripts/README.md`](../scripts/README.md)     |
| Настройки проекта          | [`../configs/README.md`](../configs/README.md)     |
| Исследовательские ноутбуки | [`../notebooks/README.md`](../notebooks/README.md) |

## Где что писать

Чтобы документация не расползалась и не устаревала, у каждого файла должна быть своя зона ответственности.

| Где                                            | Что писать                                                                        |
|------------------------------------------------|-----------------------------------------------------------------------------------|
| [`../README.md`](../README.md)                 | краткое описание проекта, демонстрация результата, быстрый запуск и ссылки дальше |
| [`docs/README.md`](README.md)                  | карта документации и порядок чтения                                               |
| [`architecture.md`](architecture.md)           | архитектурные решения и общий путь данных                                         |
| [`data_contract.md`](data_contract.md)         | контракты таблиц и границы ответственности между слоями                           |
| README внутри модулей                          | назначение модуля, основные классы, входы, выходы и ограничения                   |
| [`../scripts/README.md`](../scripts/README.md) | пользовательские команды и сценарии запуска                                       |
| [`../configs/README.md`](../configs/README.md) | настройки проекта и параметры сценариев                                           |

Если информация относится к конкретному модулю, лучше писать её в README этого модуля, а из общих документов давать
ссылку.

## Коротко

[`docs/README.md`](README.md) — это не место для повторения всего проекта.

Его задача — быстро привести к нужному документу:

| Нужно                    | Читать                                                                                                                         |
|--------------------------|--------------------------------------------------------------------------------------------------------------------------------|
| понять проект целиком    | [`../README.md`](../README.md)                                                                                                 |
| запустить проект         | [`../scripts/README.md`](../scripts/README.md)                                                                                 |
| понять архитектуру       | [`architecture.md`](architecture.md)                                                                                           |
| понять таблицы           | [`data_contract.md`](data_contract.md)                                                                                         |
| изменить конкретный слой | README нужного модуля в [`../src/ozon_similar_products/`](../src/ozon_similar_products/)                                       |
| проверить качество       | [`evaluation_metrics.md`](evaluation_metrics.md) и [`evaluation/README.md`](../src/ozon_similar_products/evaluation/README.md) |
| изменить настройки       | [`../configs/README.md`](../configs/README.md)                                                                                 |
