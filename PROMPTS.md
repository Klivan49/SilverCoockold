# SilverCoockold — промты для реализации

Готовые промты для AI-агента (Claude Code, Cursor, OpenCode и т.п.).
Идти строго по порядку, после каждого шага — проверка и коммит.

## Как пользоваться

1. Открыть проект `~/Документы/SilverCoockold`.
2. Дать агенту **Промт 0** — это контекст на всю сессию.
3. Дальше — по одному промту за раз: 1 → 2 → 3 → 4 → 5 → 6 → 7.
4. После каждого шага запустить команды из блока «Проверка» и только потом идти дальше.
5. Если агент начал «улучшать» архитектуру — остановить и напомнить правила из Промта 0.
6. AI-промт (8) — только после того, как MVP стабильно проработал сутки.

---

## Промт 0 — контекст и правила

```text
Ты — senior Python-разработчик. Мы делаем проект SilverCoockold.

Прочитай README.md в корне проекта — это источник истины по архитектуре,
конфигу и поведению. Не меняй архитектуру без моего согласия.

Цель MVP: локальный демон, который опрашивает Kufar по запросам
«серебро лом» и «серебро лот» и мгновенно присылает новые объявления
в Telegram. Без AI, без автосообщений продавцам, без VPS.

Стек: Python 3.12, httpx, aiogram 3, PyYAML, python-dotenv, SQLite (stdlib),
asyncio, pytest. Docker — только на шаге 6.

Жёсткие правила:
1. Не добавляй AI, LLM, скоринг и анализ выгоды. Только парсинг и уведомления.
2. Не добавляй автоответы и отправку сообщений продавцам.
3. Не добавляй пагинацию Kufar: только первая страница, size=100.
4. Не используй ORM, PostgreSQL, Redis, Celery, FastAPI и веб-интерфейс.
5. Все секреты — только в .env. В коде и логах токенов быть не должно.
6. HTTP-запросы к Kufar — строго последовательные, без параллельности.
7. После каждого шага: покажи список изменённых файлов, команды проверки
   и фактический вывод. Не переходи к следующему шагу без моего «дальше».

Стиль кода: простые функции, type hints, docstring на русском, без
преждевременных абстракций. Логи через стандартный logging.
```

---

## Промт 1 — скелет проекта

```text
Шаг 1. Создай скелет проекта SilverCoockold.

Создай файлы:
- app/__init__.py
- app/main.py
- app/config.py
- app/models.py
- app/kufar.py        (пока заглушка)
- app/storage.py      (пока заглушка)
- app/notifier.py     (пока заглушка)
- app/scheduler.py    (пока заглушка)
- app/analyzers/__init__.py
- app/analyzers/base.py
- app/analyzers/null.py
- tests/__init__.py
- requirements.txt
- .env.example
- .gitignore
- config.yaml

Требования:

1. requirements.txt:
   httpx, aiogram>=3, PyYAML, python-dotenv, pytest.

2. config.yaml — ровно такой:
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

3. .env.example:
   TELEGRAM_BOT_TOKEN=
   TELEGRAM_CHAT_ID=
   DB_PATH=./data/silverwatch.db
   DRY_RUN=1
   LOG_LEVEL=INFO
   PROXY_URL=
   LLM_PROVIDER=null
   LLM_BASE_URL=
   LLM_API_KEY=
   LLM_MODEL=

4. app/models.py — dataclass Ad:
   ad_id: str
   subject: str
   price_byn: int | None   # в копейках, как отдаёт Kufar
   url: str
   list_time: str | None   # ISO-8601
   raw: dict

5. app/config.py:
   - загружает .env через python-dotenv;
   - читает config.yaml;
   - dataclass Config с полями: queries, limits, telegram_token,
     telegram_chat_id, db_path, dry_run, log_level, proxy_url;
   - валидация: при DRY_RUN=0 обязательны токен и chat_id;
   - понятные ошибки на русском.

6. app/main.py — CLI на argparse:
   --check   проверить конфиг, создать папку data/, открыть/создать БД,
             вывести сводку без секретов, exit 0;
   --once    один проход по всем запросам (пока заглушка: вывести запросы);
   --daemon  рабочий режим (пока заглушка: sleep 5 и выход по Ctrl+C).
   Логирование: console + logs/app.log, уровень из env.

7. app/analyzers/base.py — Protocol Analyzer с методом analyze(ad) -> None.
   app/analyzers/null.py — NullAnalyzer, возвращает None.
   Никакого AI-кода.

8. .gitignore: .venv/, __pycache__/, .env, data/, logs/, *.pyc, .pytest_cache/

Проверка:
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  python -m app --check

Критерий готовности: команда --check завершается с кодом 0, печатает
запросы и пути, не печатает токен. Остальные команды запускаются без
ImportError.

Не делай: Kufar-клиент, БД-схему, Telegram, планировщик, Docker.
```

---

## Промт 2 — клиент Kufar

```text
Шаг 2. Реализуй клиент Kufar в app/kufar.py.

API:
  GET https://api.kufar.by/search-api/v2/search/rendered-paginated
  Параметры: query=<строка>, size=100
  Заголовки: User-Agent обычного браузера (Chrome/Linux),
             Accept: application/json
  Таймаут: из config.limits.request_timeout (20 с)

Ответ JSON:
  ads: список объектов
  total: число
  pagination: объект (не используем)

Поля объявления:
  ad_id, subject, ad_link, price_byn (копейки), list_time (ISO-8601, строка)

Требования:
1. class KufarClient с методом fetch(query: str) -> list[Ad].
2. Парсинг в Ad из app.models. price_byn: если пусто/0 — None.
3. list_time сохранять как строку.
4. Логировать: query, HTTP-статус, количество, длительность в мс.
5. Ретраи:
   - 429 и 5xx: 3 попытки, паузы 30, 60, 120 секунд;
   - таймауты и сетевые ошибки: 3 попытки с паузами 5, 15, 30;
   - после исчерпания — исключение KufarError с понятным текстом.
   Паузы делать через time.sleep или asyncio.sleep в зависимости от
   синхронного/асинхронного клиента. Выбери синхронный httpx.Client:
   планировщик вызовет его в отдельном потоке через asyncio.to_thread.
6. Поддержка PROXY_URL из конфига (httpx proxy=...).
7. Не логировать тело ответа целиком.
8. Никакой пагинации: token не передавать. size=100 и token вместе
   дают 422 — это проверено.

Дополнительно:
- scripts/fetch_fixture.py: сохраняет один реальный ответ в
  tests/fixtures/search_lom.json. Скрипт запускается вручную, в тестах
  сеть не используется.
- tests/test_kufar.py:
  * загрузка фикстуры;
  * fetch возвращает непустой список Ad;
  * price_byn парсится в int или None;
  * url и ad_id не пустые.
  Подмени HTTP-клиент фейком, который читает фикстуру.

Проверка:
  python scripts/fetch_fixture.py "серебро лом"
  pytest tests/test_kufar.py -q

Критерий готовности: тесты зелёные, реальный запрос возвращает
объявления, при 429 клиент делает паузы и не падает сразу.

Не делай: запись в БД, Telegram, планировщик.
```

---

## Промт 3 — SQLite, дедупликация, bootstrap

```text
Шаг 3. Реализуй хранилище в app/storage.py на stdlib sqlite3.

Схема ровно такая:

CREATE TABLE IF NOT EXISTS ads (
  ad_id TEXT PRIMARY KEY,
  query TEXT NOT NULL,
  subject TEXT NOT NULL,
  price_byn INTEGER,
  url TEXT NOT NULL,
  list_time TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  raw_json TEXT,
  instant_alerted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ads_first_seen ON ads(first_seen);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ad_id TEXT NOT NULL,
  query TEXT NOT NULL,
  sent_at TEXT NOT NULL,
  ok INTEGER NOT NULL,
  error TEXT
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

Требования:
1. class Storage с путём к БД; при инициализации создаёт папку и таблицы.
2. Включить WAL: PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000.
3. Методы:
   - is_new(ad_id: str) -> bool
   - save(ad: Ad, query: str) -> None   # INSERT OR IGNORE + обновление last_seen
   - mark_instant_alerted(ad_id: str) -> None
   - is_bootstrapped(query: str) -> bool
   - set_bootstrapped(query: str) -> None
   - log_alert(ad_id, query, ok, error=None) -> None
   - last_poll(query: str) -> str | None
   - set_last_poll(query: str, ts: str) -> None
   - stats() -> dict  # всего объявлений, по запросам, последний опрос
4. Время — UTC, ISO-8601 (datetime.now(timezone.utc).isoformat()).
5. raw_json — json.dumps(ad.raw, ensure_ascii=False).
6. Соединение открывать на каждый вызов или держать одно с блокировкой;
   выбери простой вариант и объясни выбор в комментарии.
7. Никакого ORM.

Тесты tests/test_storage.py (tmp_path):
- save одного ad → is_new True, повторно → False;
- после save subject/price читаются из БД;
- bootstrap-флаг сохраняется между экземплярами Storage;
- mark_instant_alerted меняет флаг;
- stats возвращает корректные числа.

Проверка:
  pytest tests/test_storage.py -q

Критерий готовности: тесты зелёные, повторный запуск не создаёт дублей,
файл БД создаётся в data/.

Не делай: Telegram, планировщик, миграции, ORM.
```

---

## Промт 4 — Telegram-уведомления

```text
Шаг 4. Реализуй отправку в Telegram в app/notifier.py на aiogram 3.

Требования:
1. class Notifier:
   - __init__(token: str | None, chat_id: str | None, dry_run: bool)
   - async send_ad(ad: Ad, query: str) -> bool
   - async send_text(text: str) -> bool
2. Формат сообщения:
   🔥 Новое: «<query>»
   <subject> — <цена> BYN
   <url>

   Цена: price_byn / 100, формат 24,99 (запятая, 2 знака).
   Если price_byn None или 0: "Цена: договорная".
3. HTML: parse_mode="HTML", экранировать &, <, > в query и subject
   через html.escape(..., quote=False).
4. DRY_RUN=1: ничего не отправлять, писать в лог строку
   "[DRY_RUN] <text одной строкой>" и возвращать True.
5. Обработка ошибок:
   - aiogram.exceptions.TelegramRetryAfter: подождать retry_after и повторить
     один раз;
   - сетевые ошибки: лог, вернуть False, не ронять процесс.
6. Не логировать токен.
7. Сессию Bot создавать один раз; добавить async close().

Тесты (без сети, DRY_RUN):
- сообщение с "<", "&" не ломает разметку;
- цена 2499 → "24,99 BYN";
- price_byn None → "договорная";
- send_ad в dry-run возвращает True.

Проверка:
  pytest tests/test_notifier.py -q
  DRY_RUN=1 python -m app --once

Критерий готовности: в dry-run в логе видно готовые сообщения,
реальный токен не нужен.

Не делай: отправку продавцам, inline-кнопки, медиа, очереди.
```

---

## Промт 5 — планировщик и мгновенные уведомления

```text
Шаг 5. Реализуй рабочую логику в app/scheduler.py и подключи её в main.py.

Требования:
1. Для каждого запроса из config.yaml — отдельная asyncio-задача.
2. Интервал: query.interval секунд + джиттер ±20%.
   Пример: 15 с → случайно 12–18 с.
3. Глобальный asyncio.Lock на HTTP-запросы: одновременно только один
   запрос к Kufar. Клиент синхронный, вызывать через asyncio.to_thread.
4. Логика прохода:
   - ads = await kufar.fetch(query)
   - для каждого ad:
     * is_new = storage.is_new(ad.ad_id)
     * storage.save(ad, query)
     * если is_new и query.notify:
         - если не bootstrap и (query.instant или не instant_alerted):
             await notifier.send_ad(ad, query)
             storage.mark_instant_alerted(ad.ad_id)
             storage.log_alert(...)
   - обновить last_poll.
5. Bootstrap:
   - если not is_bootstrapped(query):
       сохранить все ads, НЕ отправлять,
       set_bootstrapped(query),
       отправить одно сообщение:
       "✅ Запущен. «<query>»: N объявлений. Слежу за новыми."
   - bootstrap делать при первом запуске для каждого запроса отдельно.
6. Защита от лавины:
   - не более limits.max_alerts_per_cycle уведомлений за один проход;
   - если новых больше — отправить первые N, затем одно сообщение
     "Ещё M новых по запросу «...»: <ссылка на поиск>";
   - объявления старше catchup_max_age_hours не уведомлять, только save.
7. 429: клиент уже ретраит; если KufarError после ретраев — лог,
   пауза 5 минут, продолжить цикл. Одно уведомление админу
   "⚠️ Kufar 429, пауза 5 минут" не чаще одного раза в 30 минут.
8. Graceful shutdown: SIGINT/SIGTERM → отменить задачи, закрыть notifier,
   выйти с кодом 0.
9. main.py:
   --once   один проход по всем запросам, без циклов;
   --daemon запустить циклы и ждать сигнала.
10. Логи: старт/стоп, каждый проход (query, новых, отправлено, мс),
    ошибки с traceback.

Проверка:
  DRY_RUN=1 python -m app --once
  DRY_RUN=1 python -m app --daemon   # 1–2 минуты, затем Ctrl+C

Критерий готовности:
- при --once в dry-run видны сообщения только по новым объявлениям;
- повторный --once ничего не отправляет;
- --daemon переживает Ctrl+C и не оставляет задач;
- в логе видно интервалы и джиттер.

Не делай: AI, скоринг, автосообщения, пагинацию.
```

---

## Промт 6 — Docker, systemd, README

```text
Шаг 6. Подготовь запуск и перенос на VPS.

1. Dockerfile:
   - python:3.12-slim;
   - рабочая папка /app;
   - сначала COPY requirements.txt + pip install --no-cache-dir;
   - потом COPY app, config.yaml;
   - создать пользователя appuser (non-root) и папку /app/data;
   - CMD ["python", "-m", "app", "--daemon"].

2. docker-compose.yml:
   services.silverwatch:
     build: .
     restart: unless-stopped
     env_file: .env
     volumes:
       - ./data:/app/data
       - ./config.yaml:/app/config.yaml:ro
     healthcheck:
       test: ["CMD", "python", "-m", "app", "--check"]
       interval: 60s
       timeout: 10s
       retries: 3

3. .dockerignore: .venv, .git, data, logs, __pycache__, .env

4. systemd-юнит deploy/silverwatch.service:
   WorkingDirectory=/opt/SilverCoockold
   ExecStart=/opt/SilverCoockold/.venv/bin/python -m app --daemon
   Restart=always
   RestartSec=5
   EnvironmentFile=/opt/SilverCoockold/.env

5. Обнови README.md: разделы "Docker", "Переезд на VPS", "systemd",
   "Проверка здоровья". Не переписывай остальной README.

Проверка:
  docker compose build
  docker compose up -d
  docker compose ps
  docker compose exec silverwatch python -m app --check
  docker compose logs --tail=50

Критерий готовности: контейнер healthy, логи идут, папка data/
смонтирована, секреты не попали в образ.

Не делай: Kubernetes, CI/CD, nginx, базы данных.
```

---

## Промт 7 — тесты и приёмка MVP

```text
Шаг 7. Доведи проект до приёмки MVP.

1. Добавь недостающие тесты:
   - scheduler: bootstrap не отправляет уведомления;
   - scheduler: новый ad отправляется один раз;
   - scheduler: повторный проход не дублирует;
   - scheduler: лимит max_alerts_per_cycle работает;
   - catchup: старое объявление не уведомляется;
   - 429: KufarError не роняет цикл (мок).
   Используй фейковые KufarClient, Storage (или tmp SQLite) и Notifier.
   Сеть в тестах запрещена.

2. Добавь pytest.ini или настройки в pyproject:
   testpaths = tests, addopts = -q.

3. Прогони:
   pytest -q
   DRY_RUN=1 python -m app --once
   DRY_RUN=1 python -m app --once   # второй раз должен быть без сообщений

4. Проверь вручную по чек-листу и запиши результат в README:
   [ ] --check exit 0
   [ ] --once в dry-run печатает новые
   [ ] повторный --once молчит
   [ ] kill -9 + запуск не повторяет старые
   [ ] приоритетные запросы опрашиваются каждые ~15 с
   [ ] сообщение приходит за 10–20 с (с реальным токеном)
   [ ] 429 обрабатывается, процесс жив
   [ ] docker compose up работает

5. Если что-то не проходит — не «подкручивай» тесты под код.
   Сначала объясни причину, предложи фикс, потом меняй.

Критерий готовности: все тесты зелёные, чек-лист заполнен,
README содержит актуальные команды.
```

---

## Промт 8 — AI-анализатор (только после стабильного MVP)

```text
Шаг 8. Добавь AI-анализатор, не ломая приоритетные уведомления.

1. app/analyzers/base.py:
   Protocol Analyzer:
     async analyze(ad: Ad) -> Analysis | None
   dataclass Analysis:
     weight_g: float | None
     purity: int | None
     is_silver: bool | None
     price_per_gram: float | None
     scrap_value: float | None
     score: float | None
     comment: str | None
     model: str | None

2. app/analyzers/null.py: NullAnalyzer возвращает None.

3. app/analyzers/llm.py:
   - OpenAI-совместимый клиент через httpx;
   - base_url, api_key, model из env (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL);
   - provider: deepseek | openai | null;
   - промпт: извлечь вес, пробу, признак серебра, цену за грамм,
     учесть ловушки "цена за 1 грамм", "лом", "не лом", "зубр", "кокошник";
   - ответ строго JSON, валидировать поля, при ошибке — None;
   - таймаут 30 с, 1 ретрай;
   - не отправлять имя/телефон/аккаунт продавца.

4. Интеграция:
   - анализатор вызывается ТОЛЬКО для query.notify == false;
   - приоритетные instant-запросы не ждут AI;
   - результат сохраняется в таблицу analysis (ad_id PK, поля, model, created_at);
   - кэш: если запись есть, повторно не вызывать;
   - бюджет: не более LLM_MAX_CALLS_PER_DAY (env), счётчик в meta;
   - при превышении или ошибке — fallback NullAnalyzer, лог.

5. Конфиг:
   LLM_PROVIDER=null|deepseek|openai
   LLM_BASE_URL=https://api.deepseek.com
   LLM_MODEL=deepseek-chat
   LLM_MAX_CALLS_PER_DAY=200

6. Тесты: фейковый LLM-клиент, парсинг корректного JSON, битый JSON → None,
   превышение бюджета → нет вызова, instant-путь не вызывает анализатор.

Критерий готовности: при LLM_PROVIDER=null поведение MVP не меняется;
при deepseek обычные запросы получают анализ, приоритетные — нет.
```

---

## Служебные промты

### Если агент сломал проект

```text
Стоп. Не меняй код. Сначала:
1. Покажи, какие файлы изменены с последнего рабочего состояния.
2. Объясни, какая команда падает и почему.
3. Предложи минимальный фикс (1–3 файла).
4. Дождись моего подтверждения.
Не рефакторить, не переименовывать, не добавлять зависимости.
```

### Если непонятная ошибка

```text
Вот команда и полный вывод:

<вставить>

Найди корневую причину. Проверь по порядку: конфиг, env, путь к БД,
сеть, HTTP-статус, формат JSON. Не предлагай обходных путей, пока не
объяснишь причину. Дай один минимальный фикс и команду проверки.
```

### Ревью перед запуском на сутки

```text
Проведи ревью проекта без изменения кода. Проверь:
1. Утечки секретов в логи и git.
2. Гонки и параллельные запросы к Kufar.
3. Дубли уведомлений после рестарта.
4. Обработку 429, таймаутов и Ctrl+C.
5. Рост SQLite и ротацию логов.
6. Корректность UTC и ISO-времени.
7. Что приоритетные уведомления не зависят от AI.
Верни список проблем по приоритетам: critical / important / later.
```

### Генерация фикстуры

```text
Напиши scripts/fetch_fixture.py: принимает query, делает один запрос
к Kufar API с size=100, сохраняет JSON в tests/fixtures/<slug>.json,
печатает количество объявлений. Без зависимостей кроме httpx.
Не запускай его в тестах.
```
