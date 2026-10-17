# SilverCoockold

Локальный мониторинг серебра на Kufar с мгновенными уведомлениями в Telegram.

Бот следит за новыми объявлениями по запросам **«серебро лом»** и **«серебро лот»** и присылает их без скоринга и AI-проверок: появилось новое объявление — сразу уведомление. Остальные запросы («серебро», «серебряное», «серебро 925») пока просто складываются в базу для будущего анализа.

> Статус: спецификация и подготовка к разработке. Код будет добавлен по шагам из раздела «Дорожная карта».

---

## Возможности MVP

- Опрос Kufar каждые 15 секунд по приоритетным запросам.
- Мгновенное уведомление в Telegram по каждому новому объявлению из приоритетных запросов.
- Дедупликация по `ad_id` — никаких повторов после перезапуска.
- Bootstrap: при первом запуске старые объявления не спамят, приходит одно сводное сообщение.
- Устойчивость к `429`: последовательные запросы, джиттер, backoff.
- SQLite — вся история и состояние в одном файле.
- `DRY_RUN` — проверка без Telegram-токена.
- Запуск локально одной командой; позже — перенос на VPS без изменения кода.
- Заглушка `NullAnalyzer` под будущий AI (DeepSeek и другие OpenAI-совместимые API).

## Чего в MVP нет

- Автоматической переписки с продавцами.
- AI-скоринга и оценки выгоды.
- Пагинации и обхода всей выдачи.
- Веб-интерфейса и админки.
- PostgreSQL, очередей, микросервисов.

Всё это — следующие этапы, они не должны мешать главному: быстро ловить «серебро лом» и «серебро лот».

---

## Архитектура

```
config.yaml ─┐
.env ────────┼─> scheduler
             │      ├─ priority loop (15s): «серебро лом», «серебро лот»
             │      └─ normal loop (10m): «серебро», «серебряное»
             │                │
             │          kufar client (httpx, retry, 429 backoff, 1 запрос за раз)
             │                │
             │          storage (SQLite: dedupe by ad_id, bootstrap, alerts)
             │                │
             │          notifier (Telegram / DRY_RUN)
             │
             └─> later: analyzer (NullAnalyzer → DeepSeekAnalyzer)
```

Принципы:

1. **Сначала уведомление, потом анализ.** Приоритетные объявления не ждут AI.
2. **Один HTTP-запрос за раз.** Параллельные запросы к Kufar приводят к `429`.
3. **Состояние в SQLite.** Перезапуск не приводит к повторным уведомлениям.
4. **Конфиг снаружи.** Переезд на VPS — это копирование `.env`, `config.yaml` и папки `data/`.

---

## Структура проекта

```
SilverCoockold/
├── app/
│   ├── __init__.py
│   ├── main.py           # CLI: --check, --once, --daemon
│   ├── config.py         # загрузка config.yaml + .env
│   ├── models.py         # dataclass Ad
│   ├── kufar.py          # клиент API Kufar
│   ├── storage.py        # SQLite, dedupe, bootstrap
│   ├── notifier.py       # Telegram + dry-run
│   ├── scheduler.py      # циклы опроса
│   └── analyzers/
│       ├── base.py       # Protocol Analyzer
│       └── null.py       # заглушка
├── tests/
│   ├── fixtures/search_lom.json
│   ├── test_kufar.py
│   └── test_storage.py
├── config.yaml
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## Быстрый старт

### 1. Требования

- Python 3.12+
- Токен Telegram-бота
- Интернет без блокировки `kufar.by`

### 2. Установка

```bash
cd ~/Документы/SilverCoockold
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` (планируется):

```text
httpx
aiogram>=3
PyYAML
python-dotenv
pytest
```

### 3. Создание Telegram-бота

1. Открыть в Telegram `@BotFather`.
2. Отправить `/newbot`, задать имя и username.
3. Скопировать токен вида `123456789:AA...`.
4. Написать своему боту `/start`.
5. Узнать `chat_id`:

```bash
curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
```

В ответе взять `message.chat.id`.

### 4. Настройка

```bash
cp .env.example .env
```

Заполнить `.env`:

```env
TELEGRAM_BOT_TOKEN=123456789:AA...
TELEGRAM_CHAT_ID=123456789
DB_PATH=./data/silverwatch.db
DRY_RUN=1
LOG_LEVEL=INFO
PROXY_URL=
```

`DRY_RUN=1` — сообщения только пишутся в лог. Для реальной отправки поставить `DRY_RUN=0`.

### 5. Запуск

```bash
python -m app --check     # проверка конфига, БД, токена
python -m app --once      # один проход по всем запросам
python -m app --daemon    # рабочий режим
```

При первом запуске бот запишет текущие объявления в базу и пришлёт одно сообщение:

```
✅ Запущен.
«серебро лом»: 36 объявлений
«серебро лот»: 100 объявлений
Слежу за новыми.
```

Дальше — только новые.

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
```

- `interval` — пауза между опросами, секунды.
- `instant` — отправлять каждое новое объявление без проверок.
- `notify` — отправлять ли уведомления вообще (для обычных запросов пока `false`).
- `max_alerts_per_cycle` — защита от лавины после простоя.
- `catchup_max_age_hours` — старше этого возраста не уведомляем, только пишем в БД.

### `.env`

| Переменная | Обязательна | Описание |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | да | токен от BotFather |
| `TELEGRAM_CHAT_ID` | да | куда слать уведомления |
| `DB_PATH` | нет | путь к SQLite, по умолчанию `./data/silverwatch.db` |
| `DRY_RUN` | нет | `1` — не отправлять в Telegram |
| `LOG_LEVEL` | нет | `INFO`, `DEBUG` |
| `PROXY_URL` | нет | прокси для запросов к Kufar |
| `LLM_PROVIDER` | нет | `null` сейчас, позже `deepseek` |
| `LLM_BASE_URL` | нет | OpenAI-совместимый endpoint |
| `LLM_API_KEY` | нет | ключ AI |
| `LLM_MODEL` | нет | имя модели |

---

## Как это работает

### Источник данных

```
GET https://api.kufar.by/search-api/v2/search/rendered-paginated
    ?query=<urlencoded>&size=100
```

- Первая страница, до 100 объявлений.
- Сортировка по умолчанию — новые сверху (`list_time`, ISO-8601).
- `price_byn` — в копейках (2500 = 25,00 BYN).
- Авторизация не требуется.
- Пагинация в MVP не используется: `token` вместе с `size=100` отдаёт `422`.

### Дедупликация

`ad_id` — первичный ключ. Если объявление уже есть в базе, обновляется `last_seen`, уведомление не отправляется. Это работает и после перезапуска, и при пересечении запросов.

### Bootstrap

При первом запуске для каждого запроса ставится флаг `bootstrapped:<query>`. Объявления записываются, но не отправляются. Иначе первый старт превратится в 100+ сообщений.

### Простой и рестарт

- Новые объявления определяются по `ad_id`, а не по времени — задержка индексации Kufar не приводит к потере.
- Если бот лежал долго, отправляются первые `max_alerts_per_cycle` объявлений, остальные — одним дайджестом.
- Объявления старше `catchup_max_age_hours` только записываются в БД.

### Ограничения Kufar

- 4 параллельных запроса → `429 Too Many Requests`.
- Последовательные запросы с паузой 2–3 секунды работают стабильно.
- Поэтому: глобальный `asyncio.Lock`, один запрос за раз, джиттер ±20%.
- При `429`: паузы 30 → 60 → 120 секунд. Если не отпустило — сообщение админу и пауза 5 минут.
- Реальная задержка уведомления: **10–20 секунд**, а не «в ту же секунду». Это ограничение polling, а не кода.

### Формат уведомления

```
🔥 Новое: «серебро лом»
Монеты серебро — 24,99 BYN
https://www.kufar.by/item/1089730241
```

Без указанной цены:

```
🔥 Новое: «серебро лот»
Лот серебра — Цена: договорная
https://www.kufar.by/item/...
```

HTML-разметка экранируется, чтобы символы `<`, `>`, `&` не ломали сообщение.

---

## Docker

```bash
docker compose up -d --build
docker compose logs -f
docker compose exec silverwatch python -m app --check
```

`docker-compose.yml`:

```yaml
services:
  silverwatch:
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
```

---

## Переезд на VPS

Код не меняется.

1. Установить Docker и Docker Compose.
2. Скопировать проект, `.env`, `config.yaml` и папку `data/`.
3. Запустить:

```bash
docker compose up -d --build
```

4. Проверить логи и healthcheck.

Альтернатива без Docker — `systemd`:

```ini
[Unit]
Description=SilverCoockold
After=network-online.target

[Service]
WorkingDirectory=/opt/SilverCoockold
ExecStart=/opt/SilverCoockold/.venv/bin/python -m app --daemon
Restart=always
RestartSec=5
EnvironmentFile=/opt/SilverCoockold/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now silverwatch
```

Ограничение локального варианта: если компьютер уходит в сон, бот останавливается. Для круглосуточной работы отключить suspend или использовать мини-ПК/роутер/VPS.

---

## Дорожная карта

| Этап | Содержание | Результат |
|---|---|---|
| 1 | Скелет, конфиг, CLI `--check` / `--once` / `--daemon` | проект запускается |
| 2 | Клиент Kufar, retry, 429, тест на фикстуре | получаем объявления |
| 3 | SQLite, дедуп, bootstrap | нет повторов |
| 4 | Telegram, `DRY_RUN` | уведомления работают |
| 5 | Два цикла, джиттер, логи, Docker | MVP готов |
| 6 | Сутки прогона, README, VPS | стабильная работа |
| 7 | AI-анализатор, скоринг, история цен | отбор выгодных |
| 8 | Черновики сообщений продавцу | ручная отправка в один клик |

### Будущий AI

- Сейчас: `LLM_PROVIDER=null`, анализатор возвращает `None`.
- Потом: `LLM_PROVIDER=deepseek`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`.
- Замена на более мощную модель — только через env, без правок кода.
- AI никогда не задерживает приоритетные уведомления.
- В промпт не отправляются персональные данные продавца.
- Бюджет вызовов ограничивается счётчиком в `meta`.

---

## Тесты

```bash
pytest -q
```

Покрытие MVP:

- парсинг реального ответа Kufar;
- перевод `price_byn` в BYN;
- дедупликация: второй проход не создаёт дублей;
- bootstrap не отправляет старые объявления;
- экранирование HTML в сообщении;
- обработка `429` не роняет процесс.

Ручная проверка:

1. `DRY_RUN=1 python -m app --once` — в логе новые объявления.
2. Повторить — тишина.
3. `kill -9`, запустить снова — старые не повторяются.
4. Новое объявление по «серебро лом» приходит в Telegram за 10–20 секунд.

---

## Troubleshooting

| Проблема | Причина | Решение |
|---|---|---|
| `429 Too Many Requests` | слишком частые запросы | увеличить `interval`, проверить, что нет параллельных запросов, включить `PROXY_URL` |
| Уведомления не приходят | неверный `chat_id` или бот не запущен | `/start` боту, проверить `getUpdates`, `DRY_RUN=0` |
| После рестарта повторы | удалили `data/silverwatch.db` | не удалять базу, она хранит состояние |
| Первый запуск заспамил | не сработал bootstrap | проверить `meta.bootstrapped:*`, пересоздать БД |
| Пустые сообщения | HTML в названии объявления | проверить экранирование |
| Бот молчит ночью | компьютер уснул | отключить suspend или перенести на VPS |

---

## Безопасность и правовые ограничения

- Kufar не предоставляет публичный API для этой задачи; используется внутренний endpoint, он может измениться.
- Автоматический сбор данных и массовые сообщения могут нарушать пользовательское соглашение Kufar. Риск — блокировка IP и аккаунта.
- Проект рассчитан на личный низкочастотный мониторинг без авторизации и без рассылок.
- Токен бота хранится только в `.env`; `.env` не коммитится.
- Не отправляйте в AI-сервисы персональные данные продавцов.

---

## Лицензия

Личный проект. Использование на свой риск.
