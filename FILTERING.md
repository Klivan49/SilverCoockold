# Filtering

## Факты (7 дней до 28.10.2026)

- Алертов: **666**, ~**95/день**.
- По запросам: «серебро» 352, «серебряное» 205, «серебро лот» 40,
  «серебро лом» 4.
- AI-вердиктов: **441**: `ask` 334, `look` 47, `skip` 60.
- `is_silver`: true 389, false 52.
- Категории: украшение 337, другое 50, монеты 30, столовое 21, лом 3.
- **236 алертов без AI-вердикта** — ключ OpenCode Go истёк, AI ушёл в
  fallback, и бот снова начал слать regex-лиды.

Вывод: основной мусор — `ask`-лиды и fallback без AI. Нужны три уровня:
instant / digest / skip.

## Уровни

### Instant (сразу в Telegram)

- `is_below_scrap` — цена лота ниже лома;
- `is_suspicious` — подозрительно низкая цена/грамм;
- `is_strong_lead` — цена/грамм ниже 60% лома 800 (`strong_lead_ratio`),
  даже если AI `ask`; префикс `💸 СИЛЬНЫЙ ЛИД`;
- AI `look` — сразу (порог confidence пока не применяется);
- приоритетный запрос (`notify=true`) и AI `is_silver=true`,
  `verdict != skip`;
- vision нашёл вес/пробу, и цена ниже лома.

### Digest (очередь `pending_alerts`)

- AI `ask` из обычных запросов с `confidence >= AI_MIN_CONFIDENCE_DIGEST`
  (0.6);
- AI `ask` из priority, если `is_silver=false`;
- лид без AI, если `require_ai_for_leads=false`;
- остальные лиды, которые не проходят в instant.
- `ask` не режется порогом невыгодности 40%: цепочка+крестик 200 BYN без
  веса с `unprofitable_prob=0.75` и confidence 0.9 идёт в дайджест, а не в
  skip.

Дайджест: до `lead_digest_max_items` (10) ссылок, заголовок, цена,
причина/вопрос и URL, без отдельных сообщений. Проверка раз в 60 секунд,
интервал — `lead_digest_interval_min` (30). Сортировка: сначала высокий AI
`confidence`, затем меньший `price_per_gram`; сильные лиды сюда не попадают.
Остаток остаётся на следующий дайджест. Цель — 10–20 сообщений в день.

### Skip

- AI `skip`;
- AI `is_silver=false` + `skip` (одежда, цвет, бижутерия, посеребрение);
- `unprofitable_prob > AI_MAX_UNPROFITABLE_PROB` (0.4) при вердикте не `ask`
  (`look`/`skip`) — не instant и не дайджест, `instant_alerted=1`; для `ask`
  правило не применяется;
- `ask` с `confidence < AI_MIN_CONFIDENCE_DIGEST` (0.6);
- regex «не серебро» (латунь, мельхиор, ALPACCA, штаны/куртки/цвет);
- нет цены/веса/пробы и цена выше `lead_max_price_byn` (для обычных запросов);
- bootstrap/catchup;
- дубли (`ad_id` уже alerted).

## Сильные лиды

Парсер считает `is_strong_lead=true`, если есть вес и `price_per_gram <
scrap_rate(800) * strong_lead_ratio` (по умолчанию 0.6), лот серебряный и не
подозрительный. Пример: 1091933353 — цепочка 15 г за 20 BYN (1,33 BYN/г при
ломе 5,0 BYN/г): сильный лид, instant, даже если AI ответил `ask`.

Если лот одновременно ниже лома, при отправке приоритет у префикса
`💰 НИЖЕ ЛОМА`, иначе — `💸 СИЛЬНЫЙ ЛИД`.

## Невыгодность (unprofitable_prob)

Текстовая модель и vision возвращают `unprofitable_prob` (0…1): вероятность,
что покупать лот по этой цене невыгодно. Учитываются цена/грамм против лома,
нет веса/пробы/клейма, посеребрение/бижутерия, камни/недрагоценные детали,
состояние, комплектность, доставка/пересылка, риск подделки. `0` — явно
выгодно, `1` — явно невыгодно; это не `confidence`.

Планировщик берёт `max(ai, vision)`. Если `> AI_MAX_UNPROFITABLE_PROB`
(по умолчанию 0.4) и вердикт не `ask`:

- не отправлять instant и не ставить в `pending_alerts`;
- пометить `instant_alerted=1`;
- лог `AI: unprofitable_prob=0.55 > 0.40 — skip ad_id=...`.

`ask` порогом не режется — уходит в дайджест (1091935651:
`unprofitable_prob=0.75`, confidence 0.9 — не теряем). Нет AI/vision
(None, ошибка, лимит) — правило не применяется, обычный fallback. Старые
кэши без поля дают `0.0`.

## Fallback без AI

Если `LLM_PROVIDER=none` или ключ истёк (`401 api_key_expired`):

- **не отправлять** обычные `is_lead` instant;
- при `require_ai_for_leads=true` обычные лиды пропускаются;
- при `false` обычные лиды ставятся в очередь дайджеста с reason `лид`;
- отправлять только `below`/`suspicious` и приоритетные запросы;
- в лог писать «AI недоступен, fallback».

Это сразу убирает большую часть мусора при проблемах с ключом.

## Фото-фактор

Vision может:

- прочитать вес с весов (`scale_visible`, `weight_g`);
- увидеть клеймо (`hallmark`), пробу;
- отличить серебро от посеребрения/бижутерии;
- заметить камни/недрагоценные детали в лоте.

Правила:

- если vision дал вес/пробу — пересчитать `price_per_gram`/лом;
- если подтверждён hallmark 925 — effective 925, а не 800;
- если `stones_or_costume=true` — снизить confidence, не instant;
- если vision `is_silver=false` + `skip` — skip;
- если vision подтвердил серебро, но вес не виден — `ask` → digest;
- vision только для кандидатов (ask/lead/below/suspicious), не для всех.

## Конфиг

Фактические настройки (config.yaml):

```yaml
limits:
  lead_digest_interval_min: 30
  lead_digest_max_items: 10
  require_ai_for_leads: true
  strong_lead_ratio: 0.6
```

AI-настройки — в `.env` (`LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`,
`LLM_MAX_CALLS_PER_DAY`, `LLM_TIMEOUT`, `AI_MAX_UNPROFITABLE_PROB`,
`AI_MIN_CONFIDENCE_DIGEST`). Vision (`VISION_*`) — DeepSeek primary +
Claude fallback.

## Реализация

1. `pending_alerts(ad_id PK, query, reason, queued_at, sent_at)` — очередь
   дайджеста.
2. Scheduler: решение `instant/digest/skip`; для digest — INSERT и
   `instant_alerted=1` (чтобы не дублировать).
3. Задача `digest_loop` проверяет очередь раз в 60 секунд; если истёк
   `lead_digest_interval_min`, выбирает до `lead_digest_max_items`, отправляет
   одним сообщением и помечает `sent_at`. Ошибка отправки не помечает.
4. `require_ai_for_leads=true`: при `ai is None` и `is_lead` — skip; при
   `false` — очередь с reason `лид`.
5. Тесты: instant/digest/skip; fallback без ключа; дедуп; лимит; фото-промоушен.

## Метрики приёмки

- алертов/день: **10–20**, не 95;
- `below_scrap` не теряется;
- браслет/цепочка из примера попадают в дайджест/лид;
- «штаны серебряного цвета» — skip;
- при истёкшем ключе — нет потока `ask`-лидов;
- дайджест не дублирует instant.
