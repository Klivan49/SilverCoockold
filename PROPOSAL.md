# Proposal: фото-анализ и ужесточение фильтра

## Проблема

1. В Telegram приходит слишком много мусора: за 7 дней 666 алертов (~95/день),
   из 441 AI-вердикта 334 — `ask`, 47 — `look`, 60 — `skip`.
2. Часть хороших лотов теряется: браслет за 30 BYN и цепочка 925 без веса/пробы
   не отправлялись старой логикой.
3. В описаниях часто нет веса/пробы, но они есть на фото: весы, клеймо,
   бирка, проба на замке.
4. Ключ OpenCode Go истёк — AI сейчас в fallback, поэтому мусор снова вырос.

## Цель

- Смотреть 1–3 фото объявления и извлекать: вес (весы/бирка), пробу, клеймо,
  тип изделия, признаки посеребрения/бижутерии/камней.
- Отправлять меньше, но точнее: instant только для «ниже лома», подозрительных
  и сильных лидов; остальное — дайджест.
- Не терять хорошие лоты без текстовых данных.

## Не цель

- Точная ювелирная экспертиза и гарантия подлинности.
- Автоматическая переписка с продавцами.
- Оценка художественной/коллекционной ценности.
- Обход Kufar или массовый парсинг.

## Источник фото

Detail-страница Kufar в `#__NEXT_DATA__` содержит:

```
images.gallery = ["https://rms.kufar.by/v1/gallery/adim1/....jpg", ...]
images.listings = ["https://rms.kufar.by/v1/list_thumbs_2x/adim1/....jpg", ...]
```

План:

- `gallery` — для клейма/весов (выше разрешение);
- `listings` — для общего вида (меньше токенов);
- брать первые 1–3 фото; не скачивать всё;
- не хранить изображения после анализа.

## Vision-модель

Основной кандидат — **Gemini 4 Argon Contributor** через OpenCode Go:

```
https://opencode.ai/inference/go/google/v1beta/models/gemini-4-argon-contributor:generateContent
```

Плюсы: хороший OCR/vision, входит в Go. Минусы: данные могут использоваться
для обучения; лимит $60/мес; нужен отдельный adapter (Google API, не OpenAI
chat completions).

Альтернативы:

- **Claude Haiku 5.5** (`/anthropic/v1/messages`) — 30 дней retention, $15/мес;
- **GPT 6 Luna** (`/openai/v1/responses`) — 30 дней, $15/мес;
- текстовый fallback — текущий `deepseek-v4.1-flash`.

Правило: vision вызывается только для новых объявлений, где текст не дал
однозначного ответа (нет веса/пробы, `ask`, лид, подозрительная цена), и не
чаще `VISION_MAX_CALLS_PER_DAY`.

## Vision-ответ (строгий JSON)

```json
{
  "is_silver": true,
  "item_type": "украшение|лом|монеты|столовое|другое",
  "weight_g": 101.32,
  "purity": 925,
  "hallmark": "зубр|кокошник|925|нет|не видно",
  "scale_visible": true,
  "stones_or_costume": false,
  "condition": "новое|б/у|не видно",
  "confidence": 0.0,
  "notes": "1–2 фразы",
  "question": "что уточнить у продавца"
}
```

Если на фото весы — OCR числа; если клеймо — прочитать. Если не видно —
`null`/`не видно`, не выдумывать.

## Логика решения

Приоритет:

1. `below_scrap` / `suspicious` — instant, как сейчас.
2. Vision подтвердил вес/пробу и цена ниже/около лома — instant `💰/🟡`.
3. AI `look` + confidence ≥ 0.8 — instant `🟡`.
4. AI `ask` + confidence ≥ 0.9 и цена ≤ `lead_max_price_byn` — instant
   `❓ УТОЧНИТЬ`.
5. Остальные `ask`/лиды — **дайджест 1–2 раза в день**, не instant.
6. `skip`, не-серебро, одежда/цвет/посеребрение — не отправлять.
7. Нет AI/vision (ключ истёк, ошибка) — fallback: только below/suspicious и
   приоритетные запросы; обычные лиды не спамить.

## Конфиг (проект)

```env
VISION_PROVIDER=google|anthropic|openai|none
VISION_MODEL=gemini-4-argon-contributor
VISION_BASE_URL=https://opencode.ai/inference/go/google/v1beta
VISION_MAX_CALLS_PER_DAY=100
VISION_MAX_IMAGES=3
VISION_TIMEOUT=45

AI_MIN_CONFIDENCE_INSTANT=0.8
AI_MIN_CONFIDENCE_ASK=0.9
LEAD_DIGEST_INTERVAL_MIN=720
```

## Этапы

1. Документация (этот файл, SECURITY, AGENTS, ARCHITECTURE, FILTERING).
2. `app/images.py`: URL из detail, скачивание, base64, лимит размера.
3. `app/analyzers/vision.py`: Gemini adapter + JSON-валидация + fallback.
4. Интеграция в scheduler: vision только для кандидатов, кэш `ad_vision`.
5. Фильтр: instant/digest/skip, пороги confidence.
6. Тесты на моках + локальные примеры фото.
7. Dry-run на 1–2 дня, затем включить.

## Метрики приёмки

- алертов/день: цель 10–20 вместо ~95;
- доля `skip` среди AI-вердиктов растёт;
- ни один `below_scrap` не потерян;
- браслет/цепочка из примера распознаются как лид;
- vision не вызывается для bootstrap/catchup и не чаще лимита;
- при истёкшем ключе бот не спамит лидами.

## Риски

- OCR весов ошибается; число показывать как «по фото», не как факт.
- Клеймо может быть не видно/подделка; финальная проверка у продавца.
- Vision дороже текста; лимиты и кэш обязательны.
- Провайдер может изменить цены/лимиты/политику данных.
- Фото Kufar могут быть старыми/чужими; не гарантия товара.
