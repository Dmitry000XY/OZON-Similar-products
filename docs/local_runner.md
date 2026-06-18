# Local heavy GitHub Actions runner

Этот документ описывает локальный self-hosted runner для тяжелого ML pipeline проекта
`Dmitry000XY/OZON-Similar-products`.

## Схема

`GitHub button -> self-hosted runner -> Docker -> pipeline -> safe artifact`

Workflow называется `Local heavy pipeline` и запускается только вручную через
`workflow_dispatch`. Автоматических триггеров `push`, `pull_request` и
`pull_request_target` нет: публичный репозиторий не должен автоматически выполнять
чужой код на локальном ПК.

## Безопасность

Применены ограничения:

- runner label: `ozon-local-heavy`;
- runner name: `ozon-local-heavy-runner`;
- workflow runner selector: `[self-hosted, windows, x64, ozon-local-heavy]`;
- manual-only запуск через `Actions -> Local heavy pipeline -> Run workflow`;
- allowlist по `github.actor`;
- один heavy run за раз через `concurrency`;
- raw data монтируются read-only;
- Docker socket/pipe не монтируется;
- `privileged` не используется;
- весь диск `C:\` или `D:\` не монтируется;
- artifact bundle пропускает `data/raw`, `data/processed`, parquet и файлы больше
  100 MB.

Текущий allowlist:

- `Dmitry000XY` - owner/admin;
- `SvinPepe` - Никита Сычев, GitHub collaborator with write access;
- `arinaortenberg` - Арина, GitHub collaborator with write access;
- `AccidentalGenius13` - Виктор, GitHub collaborator with write access;
- `svinpepe2` - дополнительный командный аккаунт;
- `IDhide` - Илья, GitHub collaborator with write access;
- `Sleepy-Fenrir` - Семен Брыкин, GitHub collaborator with write access.

## Runtime paths

Основная runtime-папка:

```text
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

Код остается в git checkout. Тяжелые данные, processed artifacts, outputs и
runner binaries лежат вне репозитория.

## Подготовка данных

Raw data должны лежать здесь:

```text
D:\ozon-local-runner\data\raw\user_actions
D:\ozon-local-runner\data\raw\product_information
```

Если копирование raw data невозможно или долгое, допустимо временно использовать
существующий `data/raw` из рабочей копии как read-only mount, но outputs и processed
должны оставаться в `D:\ozon-local-runner`.

## Регистрация runner

1. В GitHub откройте repository settings:
   `Settings -> Actions -> Runners -> New self-hosted runner -> Windows x64`.
2. Скопируйте registration token. Не сохраняйте его в Git, docs или логи.
3. В PowerShell из корня репозитория выполните:

```powershell
.\tools\local-runner\windows\register-ozon-runner.ps1 -AsService
```

Скрипт скачивает официальный runner из `github.com/actions/runner`, регистрирует
его с именем `ozon-local-heavy-runner` и label `ozon-local-heavy`, а при `-AsService`
устанавливает Windows Service.

Для автономной настройки можно скопировать token кнопкой GitHub UI и выполнить:

```powershell
.\tools\local-runner\windows\register-ozon-runner.ps1 -AsService -TokenFromClipboard
```

Token читается из локального clipboard, не печатается и не сохраняется.

Если runner уже зарегистрирован с таким именем, скрипт остановится. Используйте
`-Replace` только после ручной проверки, что это именно нужный runner.

## Управление runner

PowerShell scripts можно запускать из терминала. Сначала перейдите в корень
репозитория:

```powershell
cd D:\ITMO\Hackathons\OZON-Similar-products
```

Статус:

```powershell
.\tools\local-runner\windows\status-ozon-runner.ps1
```

Запуск service:

```powershell
.\tools\local-runner\windows\start-ozon-runner.ps1
```

Остановка service:

```powershell
.\tools\local-runner\windows\stop-ozon-runner.ps1
```

Если runner не установлен как Windows Service, `start-ozon-runner.ps1` запускает
официальный `run.cmd` как скрытый foreground process, сохраняет PID в
`D:\ozon-local-runner\actions-runner\.runner-pid` и пишет runner log в
`D:\ozon-local-runner\logs\runner-foreground.log`.

Скрипты ищут service только по `ozon-local-heavy-runner`; если service нет, они
управляют только процессами из `D:\ozon-local-runner\actions-runner`. Docker Desktop
и чужие services не трогаются.

### Двойной клик из Проводника

Не запускайте `.ps1` через `Run with PowerShell`: окно может закрыться сразу после
ошибки или успешного завершения. Для обычного запуска двойным кликом используйте
`.cmd` wrappers:

```text
tools\local-runner\windows\start-ozon-runner.cmd
tools\local-runner\windows\stop-ozon-runner.cmd
tools\local-runner\windows\status-ozon-runner.cmd
tools\local-runner\windows\register-ozon-runner.cmd
```

Каждый `.cmd` запускает соответствующий `.ps1` с `-ExecutionPolicy Bypass`, показывает
вывод, пишет отдельный лог в `D:\ozon-local-runner\logs\` и в конце делает `pause`,
чтобы окно не закрылось.

Как понять, что runner запущен:

- `status-ozon-runner.cmd` показывает `LocallyOnline=True`;
- есть `Runner.Listener` process или Windows Service в состоянии `Running`;
- в логе есть строка `Listening for Jobs`;
- в GitHub `Settings -> Actions -> Runners` runner отображается как `Idle` или `Busy`.

### Tray monitor

Можно запустить lightweight tray monitor без сторонних зависимостей:

```text
tools\local-runner\windows\tray-ozon-runner.cmd
```

Он использует PowerShell + Windows Forms `NotifyIcon`. Tooltip показывает
`Ozon runner: running/stopped`, а меню содержит:

- `Start runner`;
- `Stop runner`;
- `Status`;
- `Open logs folder`;
- `Open GitHub Actions`;
- `Exit tray monitor`.

Если tray monitor не нужен, достаточно `.cmd` wrappers и `status-ozon-runner.cmd`.

## Запуск workflow

После merge workflow в `main`:

1. Откройте `Actions`.
2. Выберите `Local heavy pipeline`.
3. Нажмите `Run workflow`.
4. В branch dropdown выберите нужную branch/PR branch.
5. Укажите параметры.

Параметры:

- `run_mode`: `pipeline`, `lookup`, `evaluation` или `full`;
- `train_until_date`: конец окна, например `2024-04-30`;
- `lookback_days`: размер окна, для smoke test используйте `1`;
- `top_k`: top K для pipeline config override и preview;
- `config_path`: по умолчанию `configs/baseline.yaml`;
- `upload_artifact`: загружать безопасный artifact bundle.

## Docker

Базовый запуск использует:

```powershell
docker compose -p ozon-local-heavy -f docker-compose.yml -f docker-compose.local-runner.yml run --rm pipeline ...
```

`docker-compose.local-runner.yml` монтирует:

- `D:/ozon-local-runner/data/raw` -> `/app/data/raw:ro`;
- `D:/ozon-local-runner/data/processed` -> `/app/data/processed`;
- `D:/ozon-local-runner/outputs` -> `/app/outputs`;
- `D:/ozon-local-runner/artifacts` -> `/app/artifacts`.

Не используйте:

- `docker system prune`;
- `docker volume prune`;
- `docker compose down -v`;
- удаление чужих images/containers/volumes без отдельного подтверждения.

## Outputs и artifact

Локальные outputs:

```text
D:\ozon-local-runner\outputs
```

GitHub artifact получает только компактный bundle:

- `manifest.json` workflow bundle;
- `outputs/recommendations/latest/manifest.json`;
- `outputs/demo/preview_recommendations.csv`;
- `outputs/demo/preview_recommendations.json`;
- `outputs/demo/preview_metadata.json`;
- `outputs/evaluation/README.txt`, если evaluation script отсутствует;
- logs;
- file index.

Raw data, processed data, parquet и файлы больше 100 MB не загружаются.

## Troubleshooting

Runner offline:

- запустите `status-ozon-runner.ps1`;
- проверьте service status;
- проверьте GitHub `Settings -> Actions -> Runners`;
- проверьте label `ozon-local-heavy`.

Docker not running:

- откройте Docker Desktop;
- проверьте `docker info`;
- не переустанавливайте Docker без отдельного решения.

Data path missing:

- проверьте `D:\ozon-local-runner\data\raw\user_actions`;
- проверьте `D:\ozon-local-runner\data\raw\product_information`;
- raw mount должен быть read-only.

OOM:

- сначала запускайте `lookback_days=1`;
- проверьте Docker Desktop/WSL memory;
- не добавляйте `mem_limit` в compose service.

Artifact empty:

- проверьте `outputs/recommendations/latest/manifest.json`;
- проверьте `outputs/demo`;
- проверьте logs step `Build safe artifact bundle`.

Workflow button not visible:

- workflow должен быть в default branch `main`;
- Actions должны быть включены в репозитории.

Branch not visible:

- branch должна быть pushed в GitHub;
- workflow выбирается из `main`, а branch для запуска выбирается в dropdown.

Branch protection мешает merge:

- зафиксируйте текущие settings;
- временно ослабьте только нужное правило;
- после merge верните исходное состояние.

Docker path problems:

- используйте forward slash в compose для Windows paths: `D:/ozon-local-runner/...`;
- не монтируйте весь диск.

## Удаление runner

Для полного удаления runner сначала остановите service:

```powershell
.\tools\local-runner\windows\stop-ozon-runner.ps1
```

Затем в `D:\ozon-local-runner\actions-runner` выполните официальный `config.cmd remove`
с remove token из GitHub UI. Не удаляйте `D:\ozon-local-runner\data` и Docker volumes
без отдельного подтверждения.
