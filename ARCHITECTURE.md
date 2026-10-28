# Architecture

## Поток

```
Kufar search API
   ↓
ads[] (ad_id, subject, price_byn, list_time, images path)
   ↓ dedupe by ad_id (SQLite)
detail-страница (body + images.gallery/listings)
   ↓
regex-анализ (вес, проба, цена/грамм, лом, лид)
   ↓
AI текст (OpenCode Go / DeepSeek V4.1 Flash)
   ↓
AI фото (план: Gemini 4 Argon Contributor)
   ↓
решение: skip / digest / instant
   ↓
Telegram (текст + кнопка скана)
```

## Модули

| Файл | Роль |
|---|---|
| `app/kufar.py` | поиск `size=100`, detail `#__NEXT_DATA__`, retries 429/5xx, `KufarError.kind` |
| `app/models.py` | `Ad` |
| `app/storage.py` | SQLite: `ads`, `ad_details`, `ad_analysis`, `ad_ai`, `alerts`, `meta`; WAL |
| `app/silver.py` | вес/проба/цена, `is_silver`, `is_lead`, `is_suspicious`, `per_gram` |
| `app/scrap.py` | цены лома по пробам, `data/scrap_prices.json`, hallmark |
| `app/analyzers/base.py` | `AiVerdict`, `Analyzer` Protocol |
| `app/analyzers/deepseek.py` | OpenAI-compatible chat/completions, JSON, retries |
| `app/analyzers/vision.py` | (план) Gemini/Claude vision adapter |
| `app/analyzers/factory.py` | выбор `none/deepseek/openai` |
| `app/scheduler.py` | per-query циклы, bootstrap/catchup, AI, фильтр, watchdog |
| `app/notifier.py` | Telegram HTML, префиксы, inline-кнопка, dry-run |
| `app/telegram_control.py` | long polling, `/start`, `/scan`, `/status`, кулдаун |
| `app/control.py` | `data/control.json`: tracking, prevent_sleep |
| `app/status.py` | `data/status.json` |
| `app/sleep.py` | Windows `SetThreadExecutionState`, Linux `systemd-inhibit` |
| `app/healthcheck.py` | `HEALTHCHECK_URL` раз в 60 с |
| `app/systemd_notify.py` | READY/WATCHDOG |
| `ui.py` | Tkinter: статус, вкл/выкл, цены лома |
| `windows/` | install/start bat/vbs |
| `deploy/` | systemd unit для VPS |

## БД

- `ads(ad_id PK, query, subject, price_byn, url, list_time, first_seen, last_seen, raw_json, instant_alerted)`
- `ad_details(ad_id PK, body, fetched_at)`
- `ad_analysis(ad_id PK, weight_g, purity, effective_purity, price_byn, price_per_gram, scrap_price, is_below_scrap, note, parsed_at, is_suspicious, is_lead)`
- `ad_ai(ad_id PK, verdict_json, created_at)`
- `alerts(id, ad_id, query, sent_at, ok, error)`
- `meta(key PK, value)`

Флаги:
- `instant_alerted` — любое отправленное уведомление;
- `is_below_scrap` — цена лота ниже лома;
- `is_suspicious` — подозрительно низкая цена/грамм;
- `is_lead` — серебро без веса/пробы при низкой/договорной цене;
- `ad_ai.verdict_json` — `is_silver`, `verdict`, `confidence`, `reason`,
  `question`.

## Планировщик

- по задаче на запрос; приоритет 15 с, обычные 600 с, джиттер ±20%;
- глобальный HTTP-lock, `asyncio.to_thread` для sync `httpx`;
- bootstrap: сохранить, пометить, одна сводка, без detail;
- catchup: старше 12 ч — только в БД;
- 429: пауза 5 мин, уведомление не чаще 30 мин;
- stale watchdog: нет успешного опроса → алерт/восстановление;
- ручной скан `/scan` с кулдауном.

## AI-фильтр

- только новые ad, не bootstrap/catchup;
- текстовый AI: если regex `is_silver`/`is_lead`/`notify`/hint;
- кэш `ad_ai`; дневной лимит;
- `skip` → не отправлять; `ask`/`look` → отправить с `🤖`/`❓`;
- при ошибке/лимите — fallback.

## Фото (план)

- detail `images.gallery`/`listings` — публичные URL `rms.kufar.by`;
- 1–3 изображения, не хранить;
- vision JSON: `is_silver`, `item_type`, `weight_g`, `purity`, `hallmark`,
  `scale_visible`, `stones_or_costume`, `confidence`, `notes`, `question`;
- vision только для кандидатов (`ask`/lead/below/suspicious), не для всех;
- если vision нашёл вес/пробу — обновить `ad_analysis`, пересчитать лом;
- ошибка vision → текстовый fallback.

## Деплой

- локально: venv + `python -m app --daemon` / systemd user unit;
- Windows: `windows/install.bat`, `start_daemon.bat`, `start_ui.bat`,
  `start_daemon_silent.vbs`;
- VPS: Docker/compose или `deploy/silverwatch.service`;
- healthcheck: `HEALTHCHECK_URL` + systemd `WatchdogSec`.
