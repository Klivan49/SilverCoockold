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

Основная — **DeepSeek V4.1 Flash** (OpenAI-совместимый endpoint), fallback —
**Claude Haiku 5.5** через OpenCode Go:

```
POST https://opencode.ai/inference/go/openai/v1/chat/completions
Authorization: Bearer <OpenCode Go key>
x-opencode-session: ses_silverwatch_ai

POST https://opencode.ai/inference/go/anthropic/v1/messages
Authorization: Bearer <OpenCode Go key>
anthropic-version: 2023-06-01
x-opencode-session: ses_silverwatch_ai
```

Обе модели принимают base64-изображения. DeepSeek дешевле и закрывает
большинство фото; Claude подключается, когда primary не ответил или вернул
`confidence` ниже `VISION_MIN_CONFIDENCE_FALLBACK` (по умолчанию 0.5).
Тариф OpenCode Go (около $15/мес; актуальные цену и лимиты проверяйте у
провайдера). По данным провайдера, Claude хранит запросы и изображения до
30 дней; проект фото на диск не сохраняет.

Правило: vision вызывается только для новых объявлений, где текст не дал
однозначного ответа (`is_lead`/`below`/`suspicious`, AI `ask`/`look`) или для
priority-запроса, и не чаще `VISION_MAX_CALLS_PER_DAY` (лимит общий для
primary и fallback). Без ключа/провайдера — `NullVisionAnalyzer` и текстовый
fallback.

## Vision-ответ (строгий JSON)

```json
{
  "is_silver": true,
  "item_type": "украшение|лом|монеты|столовое|другое",
  "weight_g": 101.32,
  "purity": 925,
  "hallmark": "зубр|кокошник|925|875|нет|не видно",
  "scale_visible": true,
  "stones_or_costume": false,
  "confidence": 0.0,
  "notes": "1–2 фразы",
  "question": "что уточнить у продавца"
}
```

Если на фото весы — OCR числа; если клеймо — прочитать. Если не видно —
`null`/`не видно`, не выдумывать.

## Логика решения

Приоритет:

1. `below_scrap` / `suspicious` — instant.
2. Vision подтвердил вес/пробу и цена ниже лома — instant.
3. AI `look` — instant с `🤖`/`❓`.
4. Priority-запрос + AI `is_silver=true` — instant.
5. AI `ask` из обычного запроса или priority без серебра — **дайджест**,
   а не instant.
6. Vision `is_silver=false` + confidence≥0.7 + камни/другой тип — skip.
7. `skip`, одежда/цвет/посеребрение — не отправлять.
8. Нет AI/vision (ключ истёк, ошибка) — fallback: below/suspicious и
   priority; обычные лиды не спамить.

## Конфиг (проект)

```env
VISION_PROVIDER=deepseek|anthropic|none
VISION_MODEL=deepseek-v4.1-flash
VISION_BASE_URL=https://opencode.ai/inference/go/openai/v1
VISION_API_KEY=oc_sk_...       # пусто — используется LLM_API_KEY
VISION_FALLBACK_PROVIDER=anthropic|none
VISION_FALLBACK_MODEL=claude-haiku-5-5
VISION_FALLBACK_BASE_URL=https://opencode.ai/inference/go/anthropic/v1
VISION_FALLBACK_API_KEY=       # пусто — LLM_API_KEY
VISION_MIN_CONFIDENCE_FALLBACK=0.5
VISION_MAX_CALLS_PER_DAY=100
VISION_MAX_IMAGES=3
VISION_TIMEOUT=60
```

`lead_digest_interval_min` и `lead_digest_max_items` задаются в
`config.yaml` (120 и 20), а не в `.env`.

## Этапы

1. Документация (этот файл, SECURITY, AGENTS, ARCHITECTURE, FILTERING).
2. `app/images.py`: URL из detail, скачивание, base64, лимит 5 МБ.
3. `app/analyzers/vision.py`: DeepSeek + Claude адаптеры, общий JSON-парсер,
   retry/fallback.
4. Интеграция в scheduler: vision только для новых кандидатов, кэш `ad_vision`,
   дневной лимит `vision_calls:YYYY-MM-DD`.
5. Фильтр: instant/digest/skip; ask — в очередь дайджеста.
6. Тесты на моках без live-запросов и без хранения фото.
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
