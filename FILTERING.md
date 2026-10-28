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
- AI `look` с `confidence >= AI_MIN_CONFIDENCE_LOOK` (0.8);
- приоритетный запрос (`notify=true`) и AI `is_silver=true`,
  `verdict != skip`;
- vision нашёл вес/пробу, и цена ниже/около лома.

### Digest (1–2 раза в день)

- AI `ask` (все, кроме явно сильных);
- лид без AI (ключ истёк/ошибка);
- `is_lead` с ценой выше `lead_max_price_byn`;
- низкая уверенность AI;
- лиды из обычных запросов, если их больше N за проход.

Дайджест: до 20 ссылок, заголовок, цена, причина/вопрос, без отдельных
сообщений. Цель — 10–20 сообщений в день вместо ~95.

### Skip

- AI `skip`;
- AI `is_silver=false` + `skip` (одежда, цвет, бижутерия, посеребрение);
- regex «не серебро» (латунь, мельхиор, ALPACCA, штаны/куртки/цвет);
- нет цены/веса/пробы и цена выше `lead_max_price_byn` (для обычных запросов);
- bootstrap/catchup;
- дубли (`ad_id` уже alerted).

## Fallback без AI

Если `LLM_PROVIDER=none` или ключ истёк (`401 api_key_expired`):

- **не отправлять** обычные `is_lead` instant;
- отправлять только `below`/`suspicious` и приоритетные запросы;
- `ask`/лиды — в дайджест или skip;
- в лог/`/status` писать «AI недоступен, fallback».

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

```env
AI_MIN_CONFIDENCE_LOOK=0.8
AI_MIN_CONFIDENCE_ASK=0.9
LEAD_DIGEST_INTERVAL_MIN=720
LEAD_DIGEST_MAX_ITEMS=20
REQUIRE_AI_FOR_LEADS=true
VISION_MAX_CALLS_PER_DAY=100
VISION_MAX_IMAGES=3
```

## Реализация

1. `pending_alerts(ad_id PK, query, reason, queued_at, sent_at)` — очередь
   дайджеста.
2. Scheduler: решение `instant/digest/skip`; для digest — INSERT и
   `instant_alerted=1` (чтобы не дублировать).
3. Задача `digest_loop` раз в `LEAD_DIGEST_INTERVAL_MIN`: выбрать до N,
   отправить одним сообщением, пометить `sent_at`.
4. `REQUIRE_AI_FOR_LEADS=true`: при `ai is None` и `is_lead` — digest/skip.
5. Тесты: instant/digest/skip; fallback без ключа; дедуп; лимит; фото-промоушен.

## Метрики приёмки

- алертов/день: **10–20**, не 95;
- `below_scrap` не теряется;
- браслет/цепочка из примера попадают в дайджест/лид;
- «штаны серебряного цвета» — skip;
- при истёкшем ключе — нет потока `ask`-лидов;
- дайджест не дублирует instant.
