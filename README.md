# SilverCoockold

Мониторинг серебра на Kufar с уведомлениями в Telegram.

Бот каждые ~15 секунд проверяет новые объявления по запросам **«серебро лом»** и **«серебро лот»** и сразу присылает их в Telegram. Дополнительно есть простой UI, кнопка ручного скана с кулдауном и режим запрета сна компьютера.

---

## Содержание

- [Быстрый старт: Linux](#быстрый-старт-linux)
- [Быстрый старт: Windows](#быстрый-старт-windows)
- [Telegram-бот](#telegram-бот)
- [UI и управление](#ui-и-управление)
- [Конфигурация](#конфигурация)
- [Демон и автозапуск](#демон-и-автозапуск)
- [Как это работает](#как-это-работает)
- [Docker и VPS](#docker-и-vps)
- [Тесты](#тесты)
- [Проблемы и решения](#проблемы-и-решения)
- [Безопасность](#безопасность)

---

## Быстрый старт: Linux

### 1. Что нужно

- Python **3.12**
- Токен Telegram-бота
- Интернет

Проверить Python:

```bash
python3.12 --version
```

Если нет — поставь через пакетный менеджер или `uv`.

### 2. Клонировать и установить

```bash
git clone git@github.com:Klivan49/SilverCoockold.git
cd SilverCoockold

uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

Если `uv` нет:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Создать Telegram-бота и узнать `chat_id`

1. Открой `@BotFather` → `/newbot` → получи токен вида `123456789:AA...`.
2. Напиши своему боту `/start`.
3. Узнай `chat_id`:

```bash
curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
```

В ответе возьми `message.chat.id`.

> Если демон уже запущен, сначала останови его: `systemctl --user stop silverwatch`. Иначе `getUpdates` вернёт `409 Conflict` — updates забирает работающий poller.
>
> Альтернатива: написать `@userinfobot` — он покажет твой ID.

### 4. Заполнить `.env`

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Минимум:

```env
TELEGRAM_BOT_TOKEN=123456789:AA...
TELEGRAM_CHAT_IDS=123456789
TELEGRAM_ADMIN_IDS=123456789
DRY_RUN=1
```

`TELEGRAM_CHAT_IDS` и `TELEGRAM_ADMIN_IDS` — списки через запятую. Старое поле `TELEGRAM_CHAT_ID` тоже работает, если новый список пуст.

`DRY_RUN=1` — безопасный режим: сообщения только пишутся в лог.

### 5. Проверить конфигурацию

```bash
python -m app --check
```

Ожидаемый результат:

```
Запросы (4): ...
БД: .../data/silverwatch.db
Объявлений в базе: 0
Bootstrap по запросам:
  - «серебро лом»: bootstrapped=нет
  ...
Telegram bot token: ***
Telegram chat_id: ***
DRY_RUN: 1
OK
```

### 6. Сухой прогон

Чтобы не пометить реальные объявления как просмотренные, используй временную БД:

```bash
DRY_RUN=1 DB_PATH=/tmp/silverwatch_dry.db python -m app --once
```

В логе появятся `[DRY_RUN]`-сообщения, в Telegram ничего не уйдёт.

### 7. Первый реальный запуск

Поставь в `.env`:

```env
DRY_RUN=0
```

Первый запуск:

```bash
python -m app --once
```

Что произойдёт:

- бот сохранит текущие объявления в базу;
- пришлёт **4 сводки** bootstrap — по одной на запрос;
- новые объявления начнут приходить только после этого.

Повторный `--once` должен пройти молча:

```bash
python -m app --once
```

### 8. Постоянный режим

```bash
python -m app --daemon
```

Остановка — `Ctrl+C`. Для автозапуска — раздел [Демон и автозапуск](#демон-и-автозапуск).

### 9. UI

```bash
python ui.py
```

Окно показывает статус, кнопки «Включить отслеживание» / «Отключить», чекбокс «Не давать ПК спать» и хвост лога.

---

## Быстрый старт: Windows

1. Установи **Python 3.12** с [python.org](https://www.python.org/downloads/windows/).
   При установке отметь:
   - **Add python.exe to PATH**
   - **tcl/tk and IDLE** (нужно для UI)
2. Скачай проект или склонируй:

   ```bat
   git clone git@github.com:Klivan49/SilverCoockold.git
   cd SilverCoockold
   ```

3. Запусти установку:

   ```bat
   windows\install.bat
   ```

   Скрипт проверит `py -3.12`, создаст `.venv`, поставит зависимости и скопирует `.env.example` → `.env`.

4. Заполни `.env`:

   ```env
   TELEGRAM_BOT_TOKEN=123456789:AA...
   TELEGRAM_CHAT_IDS=123456789
   TELEGRAM_ADMIN_IDS=123456789
   DRY_RUN=1
   ```

5. Проверь:

   ```bat
   .venv\Scripts\activate.bat
   python -m app --check
   ```

6. Запуск:

   | Файл | Что делает |
   |---|---|
   | `windows\start_daemon.bat` | демон в консоли |
   | `windows\start_daemon_silent.vbs` | демон без окна |
   | `windows\start_ui.bat` | интерфейс |

7. Автозапуск:
   - `Win+R` → `shell:startup` → положить ярлык на `start_daemon_silent.vbs`;
   - либо Task Scheduler: триггер **At log on**, действие `wscript.exe`, аргумент — полный путь к `start_daemon_silent.vbs`, «Start in» — корень проекта.

Пути с кириллицей и пробелами поддерживаются.

---

## Telegram-бот

Бот умеет две вещи: присылать уведомления и запускать скан по кнопке.

### Команды

В меню бота:

```
/id     — Показать свой chat_id и user_id (доступно всем)
/start  — Подключить уведомления (только админы)
/scan   — Принудительный скан (только админы, кулдаун 5 мин)
/status — Состояние демона (только админы)
```

`/start` присылает сообщение с inline-кнопкой **«🔄 Принудительный скан»**.

Такая же кнопка добавляется под каждым новым алертом.

### Как подключить друга

1. Друг открывает бота и отправляет `/id` — команда доступна любому чату.
2. Бот отвечает `chat_id=..., user_id=...`.
3. Владелец добавляет `chat_id` друга в `TELEGRAM_CHAT_IDS` (получатели уведомлений), а `user_id` — в `TELEGRAM_ADMIN_IDS` (права на `/start`, `/scan` и кнопку скана).
4. Перезапускает демон: `systemctl --user restart silverwatch` на Linux или `windows\start_daemon.bat` на Windows.
5. Друг отправляет `/start` и получает кнопку.

`/start` от неизвестного пользователя не подписывает его автоматически: бот только отвечает его `chat_id` и `user_id`, чтобы передать владельцу. Если `TELEGRAM_ADMIN_IDS` пуст, админами считаются получатели из `TELEGRAM_CHAT_IDS` (или старого `TELEGRAM_CHAT_ID`).

### Принудительный скан

- Нажатие запускает внеплановый проход по всем запросам.
- Кулдаун — **300 секунд** (`limits.force_scan_cooldown_sec`).
- Повторное нажатие: «Подождите N с».
- Если скан уже идёт: «Скан уже выполняется».
- После завершения: «Скан завершён: новых N, отправлено M».
- Работает даже при выключенном отслеживании, но не меняет его состояние.
- Кнопку может нажать только пользователь из `TELEGRAM_ADMIN_IDS`; чат значения не имеет — проверяется `from_user.id`.
- Кулдаун общий для всех админов и чатов.

### Что приходит

```
🔥 Новое: «серебро лом»
Монеты серебро — 24,99 BYN
https://www.kufar.by/item/1089730241
```

Если цена не указана:

```
🔥 Новое: «серебро лот»
Лот серебра — Цена: договорная
https://www.kufar.by/item/...
```

---

## Мониторинг и алерты

### Сбои Kufar

При ошибке поиска приходит одно сообщение с причиной:

| Причина | Текст |
|---|---|
| 429 | `⚠️ Kufar: 429, пауза 5 минут` |
| сеть/таймаут | `⚠️ Kufar: нет связи, пауза 5 минут` |
| HTTP 4xx/5xx | `⚠️ Kufar: HTTP 503, пауза 5 минут` |
| JSON/структура ответа | `⚠️ Kufar: не удалось разобрать ответ` |

Уведомления об ошибках ограничены одним разом в 30 минут (`meta.last_kufar_notice_at`). После успешного прохода приходит `✅ Kufar снова отвечает`. Состояние ошибки хранится в `meta.kufar_error_*` и видно в `/status`.

### Stale poll

Раз в 30 секунд watchdog проверяет `last_poll`. Если `tracking=true`, запрос уже bootstrapped и последний успешный опрос старше `max(stale_poll_sec, interval*2 + 60)`, приходит:

```
⚠️ Нет успешных опросов «серебро лом» 5 минут
```

Повтор — не чаще раза в 30 минут на запрос (`meta.stale_alert:<query>`). После успешного опроса — `✅ Опрос «...» восстановлен`. При `tracking=false` и во время bootstrap алертов нет.

### `/status`

Только для админов. Показывает отслеживание, режим сна, число объявлений, последний опрос по каждому запросу, последнюю ошибку Kufar и остаток кулдауна скана.

### Внешний healthcheck

`HEALTHCHECK_URL` — необязательный адрес пинга. Если задан, раз в 60 секунд (только при `tracking=true` и наличии успешного опроса) выполняется GET. Ошибки только пишутся в лог, в Telegram не уходят. Пустое значение выключает healthcheck.

- **healthchecks.io:** создать Check, скопировать Ping URL, указать его в `HEALTHCHECK_URL`, перезапустить демон.
- **Uptime Kuma:** создать Push-монитор и указать его Push URL в `HEALTHCHECK_URL`.

### systemd watchdog и падение сервиса

- Приложение отправляет `READY=1` при старте и `WATCHDOG=1` каждые 30 секунд через `$NOTIFY_SOCKET`.
- В unit включены `Type=notify`, `WatchdogSec=120` и `OnFailure=silverwatch-alert.service`.
- Если процесс завис или упал, systemd перезапускает его (`Restart=always`), а `OnFailure` запускает `scripts/send_alert.py`.
- Алерт `⚠️ SilverCoockold: сервис упал или завис` уходит во все `TELEGRAM_CHAT_IDS`; ошибки отправки только пишутся в лог.
- Готовые unit-файлы для VPS: `deploy/silverwatch.service` и `deploy/silverwatch-alert.service`.
- Внешний healthcheck (`HEALTHCHECK_URL`) дополняет это: он заметит проблему, даже если systemd не смог перезапустить сервис.

---

## Ниже лома

Search API Kufar отдаёт `body=""`, поэтому вес и проба берутся с detail-страницы объявления через `#__NEXT_DATA__`. Detail загружается **только для новых объявлений**: bootstrap и catchup его не тянут, иначе это сотни запросов и `429`. Результат кэшируется в `ad_details`, разбор — в `ad_analysis`.

Как считается:

- вес ищется в тексте в граммах: `вес/масса/общий вес 15 г`, `15-грамм`, `8, 26гр`, `1,75гр`, `12 грамм`;
- годы (`1881 г.`), сантиметры и миллиметры весом не считаются;
- диапазон (`от 7.5 до 9.5 грамм`) и несколько разных весов без «общий вес» не угадываются — вес остаётся неизвестным;
- проба: 999/958/925/916/900/875/830/800/750/500, `Ag 925`, советские `84→875`, `88→916`;
- если проб несколько — берётся минимальная;
- 925 без клейма (`зубр`, `кокошник`, `клеймо`, `пломба`, `госпроба`) считается как `no_hallmark_purity` (по умолчанию 800);
- цена за грамм (`за 1 грамм`, `за грамм`, `р/г`, `руб/гр`) умножается на вес, а не сравнивается с лотом напрямую;
- явная цена в описании (`Цена 1500 рублей`) имеет приоритет над маленькой ценой в карточке, если она больше неё более чем в 1,5 раза;
- латунь, мельхиор, посеребрение и `ALPACCA-SILVER` отсеиваются — это не серебро;
- вес больше 1000 г считается нереалистичным (например, список годов `1932,1933,1934г.`) и не используется;
- слишком низкая цена за грамм (`< 1 BYN/г` или `< 15%` лома) помечается `⚠️ ПРОВЕРИТЬ`, а не `💰 НИЖЕ ЛОМА`;
- лом = цена лома для эффективной пробы × вес; «ниже лома» — если цена лота меньше этой суммы.

Уведомление «ниже лома» отправляется даже при `notify: false`, один раз на объявление. Подозрительно дешёвые объявления (`is_suspicious`) тоже приходят, но с префиксом `⚠️ ПРОВЕРИТЬ` и просьбой проверить цену/вес. Если detail не загрузился, scrap-алерта нет, а обычное уведомление по `notify` уходит без анализа.

Пример:

```
💰 НИЖЕ ЛОМА
🔥 Новое: «серебро лом»
Монеты серебро — 10,00 BYN
⚖️ 10 г · 925→800
💰 1,00 BYN/г · лом 5,00 BYN/г
https://www.kufar.by/item/...
```

Цены по умолчанию, BYN/грамм:

| Проба | 999 | 925 | 916 | 900 | 875 | 830 | 800 | 750 | 500 |
|---|---|---|---|---|---|---|---|---|---|
| BYN/г | 5.9 | 5.6 | 5.5 | 5.4 | 5.0 | 5.0 | 5.0 | 4.7 | 3.2 |

`data/scrap_prices.json`:

```json
{
  "prices": {"999": 5.9, "925": 5.6, "916": 5.5, "900": 5.4,
             "875": 5.0, "830": 5.0, "800": 5.0, "750": 4.7, "500": 3.2},
  "no_hallmark_purity": 800,
  "updated_at": "2026-10-17T11:00:00+00:00"
}
```

Файл читается в начале каждого прохода; отсутствие или битый JSON означают цены по умолчанию.

### Редактирование цен в UI

В главном окне кнопка **«Цены лома»** открывает окно 420×460:

- отдельное поле для каждой пробы: 999, 958, 925, 916, 900, 875, 830, 800, 750, 500;
- Combobox **«925 без клейма считать как»** со значениями 800 / 875 / 925; по умолчанию берётся `no_hallmark_purity` из файла (обычно 800);
- **«Сохранить»** проверяет, что каждая заполненная цена — число больше 0, и атомарно пишет `data/scrap_prices.json`;
- **«Сбросить к значениям по умолчанию»** только заполняет поля, файл меняется после «Сохранить»;
- **«Закрыть»** закрывает окно.

Пустое поле означает, что цена для этой пробы не задана (например, в файле по умолчанию нет 958). При ошибке показывается messagebox, файл остаётся прежним. Демон читает `data/scrap_prices.json` в начале каждого прохода, поэтому сохранённые цены применяются без перезапуска.

Правило 925: если в описании нет клейма (`зубр`, `кокошник`, `клеймо`, `пломба`, `госпроба`), для расчёта лома берётся проба из `no_hallmark_purity` (по умолчанию 800), а не 925.

Ограничения эвристики: вес и клеймо нужно проверять глазами по фото; парсер не учитывает состояние, камни, комплектность и наценку; диапазоны и несколько весов сознательно не угадываются.

### Лид-алерты

Лид — серебряное объявление, по которому слишком мало данных для расчёта лома:

- не хватает веса или пробы;
- цена не выше `limits.lead_max_price_byn` (по умолчанию 100 BYN), либо цена договорная (`0`/нет) и проба известна (например, 925) при `lead_negotiable: true`;
- объявление не «ниже лома» и не подозрительно дешёвое.

Лид-кандидат не всегда приходит отдельным instant-сообщением. При включённом AI `ask` из обычного запроса и `ask` из priority без подтверждённого серебра уходят в дайджест. Instant остаются `ниже лома`, подозрительная цена/вес, AI `look` и priority с `is_silver=true`.

Без AI обычные лиды пропускаются при `require_ai_for_leads: true`; если поставить `false`, они ставятся в очередь дайджеста.

Цена `0`/None показывается как `Цена: договорная`. Порядок префиксов: `💰 НИЖЕ ЛОМА` → `❓ УТОЧНИТЬ` / `⚠️ ПРОВЕРИТЬ` → `🟡 ПРОВЕРИТЬ`.

Отключить лиды: `lead_negotiable: false` и `lead_max_price_byn: 0`.

### AI-фильтр DeepSeek

AI нужен для двух вещей:

- отсеять мусор, который regex принимает за серебро: одежду, «серебряный цвет», бижутерию, посеребрение;
- объяснить, почему лот стоит смотреть, и сформулировать вопрос продавцу.

По умолчанию `LLM_PROVIDER=none` — поведение как раньше, без внешних запросов.

**OpenCode Go** (модель входит в подписку):

```env
LLM_PROVIDER=openai
LLM_BASE_URL=https://opencode.ai/inference/go/openai/v1
LLM_API_KEY=oc_sk_...
LLM_MODEL=deepseek-v4.1-flash
LLM_SESSION_ID=ses_silverwatch_ai
LLM_MAX_CALLS_PER_DAY=200
LLM_TIMEOUT=30
```

**DeepSeek напрямую** (нужен баланс на platform.deepseek.com):

```env
LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=deepseek-flash
LLM_MAX_CALLS_PER_DAY=200
LLM_TIMEOUT=20
```

Поддерживается любой OpenAI-совместимый endpoint: укажите `openai` в `LLM_PROVIDER` и свой `LLM_BASE_URL`/`LLM_MODEL`. Бесплатную модель `deepseek-v4.1-flash-free` нельзя использовать снаружи OpenCode — API отвечает `403 FreeTierError`; для бота нужен Go-ключ и платная модель из подписки Go.

Как работает:

- AI вызывается только для новых объявлений, не для bootstrap/catchup;
- только если есть шанс серебра: regex-анализ считает серебром, запрос приоритетный или в тексте есть серебро/925/875/лом/монеты/украшения;
- вердикт `skip` — уведомление не отправляется даже при `notify=true`;
- `look` — instant с блоком `🤖 причина` и `❓ вопрос`;
- `ask` из обычного запроса — в дайджест; `ask` из priority с `is_silver=true` — instant, иначе в дайджест;
- `is_silver=false` само по себе не блокирует: `skip` пропускается, `ask` уходит в дайджест;
- ответ кэшируется в `ad_ai`, повторно модель не вызывается;
- дневной лимит считается в `meta.llm_calls:YYYY-MM-DD`;
- нет ключа, таймаут, ошибка HTTP, битый JSON или превышен лимит — fallback на обычную regex-логику.

### Дайджест ask-лидов

Очередь `pending_alerts(ad_id PK, query, reason, queued_at, sent_at)` накапливает лиды, которые не должны идти instant. При постановке объявление сразу помечается `instant_alerted=1`, поэтому повторно не попадает ни в instant, ни в очередь. Причина в очереди — `ask` или `лид`.

Раз в 60 секунд проверяется, истёк ли `limits.lead_digest_interval_min` (по умолчанию 120 минут). Если есть pending:

```
🗂 Дайджест лидов (2)

1. Браслет серебряный 16 размер — 30,00 BYN
   🤖 Нужно уточнить вес и клеймо
   ❓ Подскажите точный вес и есть ли клеймо?
   https://www.kufar.by/item/...

2. Цепочка серебро — Цена: договорная
   🤖 AI не уверен в пробе
   ❓ Какая проба и вес?
   https://www.kufar.by/item/...
```

- до `limits.lead_digest_max_items` (по умолчанию 20) объявлений за раз;
- остаток остаётся на следующий дайджест;
- заголовок, цена, причина, вопрос и URL HTML-экранируются и обрезаются;
- ошибка отправки не помечает элементы — повтор при следующей проверке;
- пустой дайджест не отправляется;
- новые instant-алерты от этого не задерживаются.

AI не заменяет проверку клейма и веса: он может ошибаться, поэтому фото и вопрос продавцу всё равно важны. Автосообщений продавцам нет.

---

## UI и управление

Запуск:

```bash
cd ~/Документы/SilverCoockold
.venv/bin/python ui.py
```

Windows: `windows\start_ui.bat`.

Что есть:

- статус демона (запущен / нет связи);
- состояние отслеживания;
- количество объявлений в базе;
- последний опрос;
- кнопки **«Включить отслеживание»** и **«Отключить»**;
- чекбокс **«Не давать ПК спать»**;
- последние 15 строк лога.

Кнопки работают без перезапуска демона: UI пишет `data/control.json`, демон читает его раз в секунду.

### `data/control.json`

```json
{
  "tracking": true,
  "prevent_sleep": false,
  "updated_at": "2026-10-17T11:00:00+00:00"
}
```

- `tracking=false` — демон не делает запросы и не отправляет уведомления, но процесс живёт.
- `prevent_sleep=true` — запрет ухода компьютера в сон.
- Нет файла или битый JSON — значения по умолчанию: `tracking=true`, `prevent_sleep=false`.

### `data/status.json`

Пишется демоном каждые 5 секунд: `running`, `tracking`, `prevent_sleep`, `ads_total`, `last_poll`, `updated_at`. UI считает демон живым, если файл обновлялся менее 30 секунд назад.

### Режим сна

Чекбокс **«Не давать ПК спать»**:

- **Windows** — `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`;
- **Linux** — `systemd-inhibit --what=sleep --who=SilverCoockold --why=monitoring sleep infinity`.

Честное ограничение: если компьютер **уже ушёл в сон (S3)**, процесс приостановлен и бот не работает. Это нельзя обойти локальным кодом.

Варианты:
- держать ПК включённым (галочка выше);
- wake timers в BIOS/Task Scheduler — компьютер проснётся, но во время сна демон всё равно не выполняется;
- VPS — единственный способ работать 24/7.

---

## Конфигурация

### `config.yaml`

```yaml
queries:
  - q: "серебро лом"
    interval: 15
    instant: true
    notify: true
  - q: "серебро лот"
    interval: 15
    instant: true
    notify: true
  - q: "серебро"
    interval: 600
    instant: false
    notify: false
  - q: "серебряное"
    interval: 600
    instant: false
    notify: false

limits:
  max_alerts_per_cycle: 20
  catchup_max_age_hours: 12
  request_timeout: 20
  force_scan_cooldown_sec: 300
  stale_poll_sec: 180
  lead_max_price_byn: 100
  lead_negotiable: true
  require_ai_for_leads: true
  lead_digest_interval_min: 120
  lead_digest_max_items: 20
```

| Поле | Смысл |
|---|---|
| `q` | поисковый запрос Kufar |
| `interval` | пауза между опросами, секунды |
| `instant` | уведомлять сразу, без анализа |
| `notify` | отправлять ли уведомления по этому запросу |
| `max_alerts_per_cycle` | максимум сообщений за один проход |
| `catchup_max_age_hours` | старше этого возраста — только в базу, без уведомления |
| `request_timeout` | таймаут HTTP к Kufar |
| `force_scan_cooldown_sec` | кулдаун кнопки ручного скана |
| `stale_poll_sec` | порог stale-опроса, секунды (по умолчанию 180) |
| `lead_max_price_byn` | максимальная цена лида, BYN (по умолчанию 100) |
| `lead_negotiable` | слать лиды с договорной ценой и известной пробой |
| `require_ai_for_leads` | без AI обычные лиды пропускаются (fallback) |
| `lead_digest_interval_min` | интервал дайджеста ask-лидов, минуты (120) |
| `lead_digest_max_items` | максимум лидов в одном дайджесте (20) |

После изменения `config.yaml` перезапусти демон:

```bash
systemctl --user restart silverwatch
```

### `.env`

| Переменная | Обязательна | Описание |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | да | токен от `@BotFather` |
| `TELEGRAM_CHAT_IDS` | да* | получатели уведомлений через запятую |
| `TELEGRAM_CHAT_ID` | для совместимости | старый одиночный получатель; используется, если `TELEGRAM_CHAT_IDS` пуст |
| `TELEGRAM_ADMIN_IDS` | нет | `user_id` админов через запятую; если пусто — получатели |
| `DB_PATH` | нет | путь к SQLite, по умолчанию `./data/silverwatch.db` |
| `DRY_RUN` | нет | `1` — не отправлять в Telegram |
| `LOG_LEVEL` | нет | `INFO`, `DEBUG` |
| `PROXY_URL` | нет | прокси для Kufar |
| `HEALTHCHECK_URL` | нет | внешний healthcheck; пусто — выключен |
| `LLM_PROVIDER` | нет | `none` (по умолчанию), `deepseek`, `openai` |
| `LLM_BASE_URL` | нет | OpenAI-совместимый endpoint, по умолчанию `https://api.deepseek.com/v1` |
| `LLM_API_KEY` | нет | ключ AI; без него AI выключен |
| `LLM_MODEL` | нет | модель, по умолчанию `deepseek-chat` |
| `LLM_MAX_CALLS_PER_DAY` | нет | дневной лимит вызовов, по умолчанию 200 |
| `LLM_TIMEOUT` | нет | таймаут AI-запроса, секунды, по умолчанию 20 |

\* Достаточно `TELEGRAM_CHAT_IDS` или старого `TELEGRAM_CHAT_ID` — хотя бы одно поле.

Файл `.env` не коммитится. Права должны быть `600`.

---

## Демон и автозапуск

### Linux: systemd user service

Создай unit:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/silverwatch.service <<'EOF'
[Unit]
Description=SilverCoockold — мониторинг серебра на Kufar
After=network-online.target
Wants=network-online.target
OnFailure=silverwatch-alert.service

[Service]
Type=notify
WorkingDirectory=%h/Документы/SilverCoockold
ExecStart=%h/Документы/SilverCoockold/.venv/bin/python -m app --daemon
EnvironmentFile=%h/Документы/SilverCoockold/.env
Restart=always
RestartSec=5
WatchdogSec=120

[Install]
WantedBy=default.target
EOF
```

Создай companion unit для алерта при падении/зависании:

```bash
cat > ~/.config/systemd/user/silverwatch-alert.service <<'EOF'
[Unit]
Description=SilverCoockold failure alert

[Service]
Type=oneshot
WorkingDirectory=%h/Документы/SilverCoockold
ExecStart=%h/Документы/SilverCoockold/.venv/bin/python %h/Документы/SilverCoockold/scripts/send_alert.py
EnvironmentFile=-%h/Документы/SilverCoockold/.env
EOF
```

`Type=notify` ждёт `READY=1` от приложения; `WATCHDOG=1` отправляется каждые 30 секунд, а `WatchdogSec=120` перезапускает зависший сервис. `OnFailure` вызывает `silverwatch-alert.service`, которая отправляет алерт во все `TELEGRAM_CHAT_IDS`.

Включи:

```bash
systemctl --user daemon-reload
systemctl --user enable --now silverwatch
loginctl enable-linger "$USER"
```

Управление:

```bash
systemctl --user status silverwatch
journalctl --user -u silverwatch -f
systemctl --user restart silverwatch
systemctl --user stop silverwatch
```

Логи также пишутся в `logs/app.log` с ротацией 5 MB × 3.

> Если проект лежит в другом каталоге, поправь `WorkingDirectory`, `ExecStart` и `EnvironmentFile`.

### Windows

- `windows\start_daemon.bat` — запуск в консоли;
- `windows\start_daemon_silent.vbs` — без окна;
- автозапуск — через `shell:startup` или Task Scheduler.

---

## Как это работает

### Источник данных

```
GET https://api.kufar.by/search-api/v2/search/rendered-paginated
    ?query=<urlencoded>&size=100
```

- первая страница, до 100 объявлений;
- сортировка по умолчанию — новые сверху (`list_time`);
- `price_byn` — в копейках (`2499` = 24,99 BYN);
- авторизация не нужна;
- пагинация не используется: `token` + `size=100` даёт `422`.

### Дедупликация

`ad_id` — первичный ключ SQLite. Повторный проход обновляет `last_seen` и не отправляет уведомление. Работает и после перезапуска.

### Bootstrap

При первом запуске по каждому запросу:

1. скачать объявления;
2. сохранить;
3. пометить как обработанные;
4. отправить одну сводку.

Так первый старт не превращается в 100+ сообщений.

### Простой и рестарт

- Новые объявления определяются по `ad_id`, а не по времени.
- После долгого простоя отправляются первые `max_alerts_per_cycle`, остальные — одним дайджестом.
- Объявления старше `catchup_max_age_hours` только сохраняются.

### Ограничения Kufar

- 4 параллельных запроса → `429`.
- Последовательные запросы с паузой 2–3 с работают стабильно.
- Бот использует глобальный `asyncio.Lock` и один HTTP-запрос за раз.
- При `429`: паузы 30 → 60 → 120 с, затем пауза 5 минут и уведомление админу не чаще раза в 30 минут.
- Реальная задержка уведомления — **10–20 секунд**, это ограничение polling.

---

## Docker и VPS

Docker нужен только для VPS. Локально достаточно venv.

```bash
docker compose up -d --build
docker compose logs -f
docker compose exec silverwatch python -m app --check
```

`docker-compose.yml` монтирует `./data` и `./config.yaml`, healthcheck выполняет `--check` каждые 60 секунд.

Для VPS:

1. Установить Docker и Docker Compose.
2. Скопировать проект, `.env`, `config.yaml`, папку `data/`.
3. `docker compose up -d --build`.

Без Docker используй unit из `deploy/silverwatch.service`, поправив пути на `/opt/SilverCoockold`.

---

## Тесты

```bash
.venv/bin/python -m pytest -q
```

Ожидаемо: **175 passed**.

Проверяются:

- парсинг Kufar и ретраи;
- SQLite, дедупликация, bootstrap;
- Telegram-форматирование и dry-run;
- планировщик, лимиты, catchup, 429;
- UI-контроль, `control.json`, `status.json`;
- кулдаун ручного скана и защита от параллельных сканов.

---

## Проблемы и решения

| Проблема | Причина | Решение |
|---|---|---|
| `429 Too Many Requests` | слишком частые запросы | увеличить `interval`, проверить, что нет второго процесса, включить `PROXY_URL` |
| Уведомления не приходят | неверный токен/`chat_id`, бот не запущен | `/start` боту, проверить `.env`, `systemctl --user status silverwatch` |
| `409 Conflict` при `getUpdates` | уже работает poller | остановить демон: `systemctl --user stop silverwatch` |
| Кнопка скана не реагирует | poller не запущен или упал | `journalctl --user -u silverwatch -f`, проверить `Telegram poller запущен` |
| После рестарта повторы | удалили `data/silverwatch.db` | не удалять базу, она хранит состояние |
| Первый запуск заспамил | не сработал bootstrap | проверить `meta.bootstrapped:*` |
| Бот молчит ночью | компьютер ушёл в сон | включить «Не давать ПК спать» или перенести на VPS |
| Windows: `py` не найден | Python установлен без лаунчера | переустановить с python.org, отметить Add to PATH |
| UI не открывается | нет tkinter | установить компонент tcl/tk (Windows) или `python3-tk` (Linux) |

---

## Безопасность

- Токен и `chat_id` хранятся только в `.env`, файл с правами `600` и в `.gitignore`.
- Не коммить `.env`, `data/`, `logs/`.
- Если токен засветился — `@BotFather` → `/revoke` → новый токен, затем обновить `.env` и перезапустить демон.
- Kufar не предоставляет публичный API для этой задачи; используется внутренний endpoint. Он может измениться.
- Автоматический сбор данных и массовые сообщения могут нарушать пользовательское соглашение Kufar. Проект рассчитан на личный низкочастотный мониторинг.
- Не отправляй в будущие AI-сервисы персональные данные продавцов.

---

## Структура проекта

```
SilverCoockold/
├── app/
│   ├── main.py              # CLI: --check, --once, --daemon
│   ├── config.py            # config.yaml + .env
│   ├── kufar.py             # клиент Kufar
│   ├── storage.py           # SQLite, дедуп, bootstrap
│   ├── notifier.py          # Telegram-отправка
│   ├── scheduler.py         # циклы опроса, ручной скан
│   ├── telegram_control.py  # /start, /scan, кнопка, кулдаун
│   ├── control.py           # control.json
│   ├── status.py            # status.json
│   └── sleep.py             # запрет сна
├── ui.py                    # Tkinter UI
├── windows/                 # bat/vbs для Windows
├── deploy/                  # systemd unit для VPS
├── tests/
├── config.yaml
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## Лицензия

Личный проект. Использование на свой риск.
