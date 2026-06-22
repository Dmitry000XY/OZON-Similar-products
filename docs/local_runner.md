# Локальный runner для тяжёлых запусков

Этот документ описывает локальный self-hosted runner для тяжёлых запусков проекта **OZON Similar Products**.

Runner нужен для сценариев, которые неудобно выполнять на обычных GitHub-hosted runners: например, когда нужны локальные
данные, больше ресурсов или длительный запуск полного конвейера.

Связанные документы:

* [корневой README](../README.md);
* [команды запуска](../scripts/README.md);
* [настройки проекта](../configs/README.md);
* [README модуля `pipeline`](../src/ozon_similar_products/pipeline/README.md);
* [README модуля `evaluation`](../src/ozon_similar_products/evaluation/README.md).

## Общая схема

```
GitHub Actions
→ self-hosted runner на локальном компьютере
→ Docker
→ pipeline / evaluation / lookup
→ безопасный artifact bundle
```

Workflow называется:

```
Local heavy pipeline
```

Он запускается только вручную через `workflow_dispatch`.

Автоматических запусков на `push`, `pull_request` и `pull_request_target` нет. Это важно: публичный репозиторий не
должен автоматически выполнять чужой код на локальном компьютере.

## Когда использовать этот runner

Локальный runner нужен, когда:

* нужно прогнать тяжёлый конвейер на локальных данных;
* обычный GitHub-hosted runner не подходит по ресурсам;
* данные нельзя или неудобно загружать в GitHub Actions;
* нужно получить безопасный компактный artifact без raw- и processed-данных.

Для обычной разработки, unit-тестов и лёгких проверок этот runner не нужен.

Обычные локальные команды описаны в [README скриптов](../scripts/README.md).

## Ограничения безопасности

Для runner применяются ограничения:

* label runner: `ozon-local-heavy`;
* имя runner: `ozon-local-heavy-runner`;
* selector workflow: `[self-hosted, windows, x64, ozon-local-heavy]`;
* запуск только вручную через `Actions → Local heavy pipeline → Run workflow`;
* allowlist по `github.actor`;
* только один тяжёлый запуск одновременно через `concurrency`;
* raw-данные монтируются только на чтение;
* Docker socket/pipe не монтируется;
* `privileged` не используется;
* весь диск `C:\` или `D:\` не монтируется;
* artifact bundle не включает `data/raw`, `data/processed`, parquet-файлы и файлы больше 100 MB.

Текущий allowlist:

* `Dmitry000XY` — owner/admin;
* `SvinPepe` — Никита Сычев, GitHub collaborator with write access;
* `arinaortenberg` — Арина, GitHub collaborator with write access;
* `AccidentalGenius13` — Виктор, GitHub collaborator with write access;
* `svinpepe2` — дополнительный командный аккаунт;
* `IDhide` — Илья, GitHub collaborator with write access;
* `Sleepy-Fenrir` — Семён Брыкин, GitHub collaborator with write access.

## Локальная рабочая папка

Основная папка runner:

```
D:\ozon-local-runner\
  data\
    raw\
      user_actions\
      product_information\
    processed\
  outputs\
  artifacts\
  logs\
  actions-runner\
```

Код проекта остаётся в git checkout.

Тяжёлые данные, processed-артефакты, outputs, artifacts, logs и файлы самого runner лежат вне репозитория.

Так мы не смешиваем рабочую копию кода и тяжёлые локальные данные.

## Данные для запуска

Raw-данные должны лежать здесь:

```
D:\ozon-local-runner\data\raw\user_actions
D:\ozon-local-runner\data\raw\product_information
```

Если скопировать raw-данные в эту папку долго или временно невозможно, можно использовать существующий `data/raw` из
рабочей копии как read-only mount.

Но outputs и processed-данные всё равно должны оставаться здесь:

```
D:\ozon-local-runner\
```

Подготовка исходных данных для обычного локального запуска описана в [README скриптов](../scripts/README.md).

## Регистрация runner

1. В GitHub откройте настройки репозитория:

   Settings → Actions → Runners → New self-hosted runner → Windows x64

2. Скопируйте registration token.

Не сохраняйте token в Git, документацию или логи.

3. В PowerShell из корня репозитория выполните:

   .\tools\local-runner\windows\register-ozon-runner.ps1 -AsService

Скрипт [`register-ozon-runner.ps1`](../tools/local-runner/windows/register-ozon-runner.ps1) скачивает официальный runner
из `github.com/actions/runner`, регистрирует его с именем:

```
ozon-local-heavy-runner
```

и label:

```
ozon-local-heavy
```

Если передан `-AsService`, runner устанавливается как Windows Service.

Для автономной настройки можно скопировать token через GitHub UI и выполнить:

```
.\tools\local-runner\windows\register-ozon-runner.ps1 -AsService -TokenFromClipboard
```

Token читается из локального clipboard, не печатается и не сохраняется.

Если runner уже зарегистрирован с таким именем, скрипт остановится. Используйте `-Replace` только после ручной проверки,
что это именно нужный runner.

## Управление runner

PowerShell-скрипты нужно запускать из корня репозитория.

Пример:

```
cd D:\ITMO\Hackathons\OZON-Similar-products
```

Проверить статус:

```
.\tools\local-runner\windows\status-ozon-runner.ps1
```

Запустить service:

```
.\tools\local-runner\windows\start-ozon-runner.ps1
```

Остановить service:

```
.\tools\local-runner\windows\stop-ozon-runner.ps1
```

Основные файлы управления:

| Назначение   | PowerShell                                                                           | CMD wrapper                                                                          |
|--------------|--------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| Регистрация  | [`register-ozon-runner.ps1`](../tools/local-runner/windows/register-ozon-runner.ps1) | [`register-ozon-runner.cmd`](../tools/local-runner/windows/register-ozon-runner.cmd) |
| Старт        | [`start-ozon-runner.ps1`](../tools/local-runner/windows/start-ozon-runner.ps1)       | [`start-ozon-runner.cmd`](../tools/local-runner/windows/start-ozon-runner.cmd)       |
| Стоп         | [`stop-ozon-runner.ps1`](../tools/local-runner/windows/stop-ozon-runner.ps1)         | [`stop-ozon-runner.cmd`](../tools/local-runner/windows/stop-ozon-runner.cmd)         |
| Статус       | [`status-ozon-runner.ps1`](../tools/local-runner/windows/status-ozon-runner.ps1)     | [`status-ozon-runner.cmd`](../tools/local-runner/windows/status-ozon-runner.cmd)     |
| Tray monitor | [`tray-ozon-runner.ps1`](../tools/local-runner/windows/tray-ozon-runner.ps1)         | [`tray-ozon-runner.cmd`](../tools/local-runner/windows/tray-ozon-runner.cmd)         |

Если runner не установлен как Windows Service, [
`start-ozon-runner.ps1`](../tools/local-runner/windows/start-ozon-runner.ps1) запускает официальный `run.cmd` как
скрытый foreground process.

В этом случае скрипт:

* сохраняет PID в `D:\ozon-local-runner\actions-runner\.runner-pid`;
* пишет лог runner в `D:\ozon-local-runner\logs\runner-foreground.log`.

Скрипты ищут service только по имени:

```
ozon-local-heavy-runner
```

Если такого service нет, они управляют только процессами из:

```
D:\ozon-local-runner\actions-runner
```

Docker Desktop и чужие services эти скрипты не трогают.

## Запуск через двойной клик

Не запускайте `.ps1` через `Run with PowerShell`: окно может закрыться сразу после ошибки или успешного завершения.

Для запуска двойным кликом используйте `.cmd` wrappers:

```
tools\local-runner\windows\start-ozon-runner.cmd
tools\local-runner\windows\stop-ozon-runner.cmd
tools\local-runner\windows\status-ozon-runner.cmd
tools\local-runner\windows\register-ozon-runner.cmd
```

Каждый `.cmd`:

* запускает соответствующий `.ps1` с `-ExecutionPolicy Bypass`;
* показывает вывод;
* пишет отдельный лог в `D:\ozon-local-runner\logs\`;
* в конце делает `pause`, чтобы окно не закрылось.

## Как понять, что runner работает

Runner запущен, если выполняются признаки:

* `status-ozon-runner.cmd` показывает `LocallyOnline=True`;
* есть процесс `Runner.Listener` или Windows Service в состоянии `Running`;
* в логе есть строка `Listening for Jobs`;
* в GitHub `Settings → Actions → Runners` runner отображается как `Idle` или `Busy`.

## Tray monitor

Можно запустить лёгкий tray monitor без сторонних зависимостей:

```
tools\local-runner\windows\tray-ozon-runner.cmd
```

Он использует PowerShell + Windows Forms `NotifyIcon`.

Tooltip показывает:

```
Ozon runner: running/stopped
```

Меню содержит:

* `Start runner`;
* `Stop runner`;
* `Status`;
* `Open logs folder`;
* `Open GitHub Actions`;
* `Exit tray monitor`.

Если tray monitor не нужен, достаточно `.cmd` wrappers и `status-ozon-runner.cmd`.

## Запуск workflow

После merge workflow в `main`:

1. Откройте `Actions`.
2. Выберите `Local heavy pipeline`.
3. Нажмите `Run workflow`.
4. В branch dropdown выберите нужную branch или PR branch.
5. Укажите параметры запуска.

Параметры:

| Параметр           | Что означает                                                 |
|--------------------|--------------------------------------------------------------|
| `run_mode`         | режим запуска: `pipeline`, `lookup`, `evaluation` или `full` |
| `train_until_date` | конец train-окна, например `2024-04-30`                      |
| `lookback_days`    | размер окна; для smoke test используйте `1`                  |
| `top_k`            | top-K для pipeline config override и preview                 |
| `config_path`      | путь к конфигу; по умолчанию `configs/baseline.yaml`         |
| `upload_artifact`  | загружать ли безопасный artifact bundle                      |

В обычном локальном режиме похожие сценарии запускаются через console scripts из [`pyproject.toml`](../pyproject.toml),
например `ozon-run-pipeline`, `ozon-run-full`, `ozon-run-tune` и `ozon-preview-recommendations`. Подробнее —
в [README скриптов](../scripts/README.md).

## Docker

Базовый запуск использует Docker Compose:

```
docker compose -p ozon-local-heavy -f docker-compose.yml -f docker-compose.local-runner.yml run --rm pipeline ...
```

[`docker-compose.local-runner.yml`](../docker-compose.local-runner.yml) монтирует:

| Локальный путь                        | Путь внутри контейнера |
|---------------------------------------|------------------------|
| `D:/ozon-local-runner/data/raw`       | `/app/data/raw:ro`     |
| `D:/ozon-local-runner/data/processed` | `/app/data/processed`  |
| `D:/ozon-local-runner/outputs`        | `/app/outputs`         |
| `D:/ozon-local-runner/artifacts`      | `/app/artifacts`       |

Не используйте без отдельного решения:

```
docker system prune
docker volume prune
docker compose down -v
```

Также не удаляйте чужие images, containers и volumes без отдельного подтверждения.

## Outputs и artifact bundle

Локальные outputs сохраняются здесь:

```
D:\ozon-local-runner\outputs
```

В GitHub artifact попадает только компактный bundle:

* `manifest.json` workflow bundle;
* `outputs/latest/manifest.json`;
* `outputs/demo/preview_recommendations.csv`;
* `outputs/demo/preview_recommendations.json`;
* `outputs/demo/preview_metadata.json`;
* `outputs/evaluation/README.txt`, если evaluation script отсутствует;
* logs;
* file index.

В GitHub artifact не попадают:

* raw-данные;
* processed-данные;
* parquet-файлы;
* файлы больше 100 MB.

## Если что-то пошло не так

### Runner offline

Проверьте:

```
.\tools\local-runner\windows\status-ozon-runner.ps1
```

Затем проверьте:

* состояние Windows Service;
* GitHub `Settings → Actions → Runners`;
* label `ozon-local-heavy`.

### Docker не запущен

Проверьте:

```
docker info
```

Также убедитесь, что открыт Docker Desktop.

Не переустанавливайте Docker без отдельного решения.

### Не найден путь к данным

Проверьте, что существует папка:

```
D:\ozon-local-runner\data\raw\user_actions
```

И справочник товаров:

```
D:\ozon-local-runner\data\raw\product_information
```

### Artifact слишком большой

Проверьте, что bundle не включает:

```
data/raw
data/processed
*.parquet
```

и файлы больше 100 MB.

## Связанные документы

* [корневой README](../README.md) — краткое описание проекта и быстрый запуск;
* [README скриптов](../scripts/README.md) — обычные команды запуска;
* [README конфигов](../configs/README.md) — настройки проекта;
* [архитектура проекта](architecture.md) — общий путь данных;
* [README модуля `pipeline`](../src/ozon_similar_products/pipeline/README.md) — полный запуск конвейера;
* [README модуля `evaluation`](../src/ozon_similar_products/evaluation/README.md) — оценка качества;
* [`docker-compose.local-runner.yml`](../docker-compose.local-runner.yml) — Docker Compose override для локального
  runner;
* [`tools/local-runner/windows/`](../tools/local-runner/windows/) — скрипты управления runner.

## Коротко

Локальный runner нужен только для тяжёлых запусков.

Он запускается вручную, использует отдельную локальную папку `D:\ozon-local-runner\`, монтирует raw-данные только на
чтение и загружает в GitHub только безопасный компактный artifact bundle.

Главные правила:

```
не запускать чужой код автоматически
не монтировать весь диск
не монтировать Docker socket/pipe
не загружать raw/processed/parquet в artifact
не использовать prune-команды без отдельного решения
```
