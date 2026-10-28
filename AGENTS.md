# AGENTS.md — правила для AI-агентов

Читать перед изменениями: `README.md`, `SECURITY.md`, `ARCHITECTURE.md`,
`PROPOSAL.md`, `FILTERING.md`.

## Инварианты

1. Только личный низкочастотный мониторинг Kufar. Без обхода защиты и массовых
   сообщений.
2. Один Telegram-токен = один poller. Не запускать второй демон с тем же токеном.
3. Секреты только в `.env`, права `600`, не коммитить и не логировать.
4. Не отправлять продавцам сообщения автоматически.
5. Не трогать `data/`, `logs/`, `.env` без явной просьбы. БД — состояние
   дедупликации; удаление ломает bootstrap.
6. Не делать live-запросы к Kufar/AI в тестах. Только фейки/фикстуры.
7. Не менять поведение bootstrap/catchup без тестов: старые объявления не
   должны спамить.
8. Не добавлять зависимости без согласования. `httpx`, `aiogram`, `PyYAML`,
   `python-dotenv`, `pytest` — базовые.
9. Все изменения — с тестами и обновлением документации.
10. После изменения кода не перезапускать сервис, если об этом не просили.

## Команды

```bash
cd ~/Документы/SilverCoockold
.venv/bin/python -m pytest -q
.venv/bin/python -m app --check
.venv/bin/python -m app --once      # один проход
.venv/bin/python -m app --daemon    # рабочий режим
systemctl --user status silverwatch
```

Тесты: `pytest -q` (сейчас 211+). Новые тесты — без сети, с `tmp_path`,
`httpx.MockTransport`/фейковым ботом.

## Стиль

- Python 3.12, `from __future__ import annotations`, type hints.
- Русские docstring и сообщения.
- Простые функции, без ORM и микросервисов.
- SQLite: `ALTER TABLE ... ADD COLUMN` только в try/except, без destructive
  миграций.
- Время — UTC ISO-8601.
- Логи через `logging`, без токенов/ключей/телефонов.

## Где что

- `app/kufar.py` — поиск, detail, retries, `KufarError.kind`.
- `app/storage.py` — SQLite, дедуп, `ad_ai`, `ad_analysis`.
- `app/silver.py` — вес/проба/цена, `is_silver`, `is_lead`, `is_suspicious`.
- `app/analyzers/deepseek.py` — текстовый AI (OpenAI-compatible).
- `app/analyzers/vision.py` — фото-анализ (план/реализация).
- `app/scheduler.py` — циклы, bootstrap, AI, фильтр, watchdog.
- `app/notifier.py` — Telegram-сообщения, префиксы, кнопка.
- `app/telegram_control.py` — `/start`, `/scan`, `/status`, кулдаун.
- `ui.py` — Tkinter, цены лома и статус.
- `windows/`, `deploy/` — запуск.

## AI/vision

- Вход: только публичные title/body/price/category/region/фото товара.
- Не отправлять имя/телефон/username продавца.
- Текстовый AI: `LLM_*`; vision: `VISION_*`.
- Все ответы — строгий JSON, валидировать; при ошибке/таймауте/429 — fallback,
  не блокировать priority-алерты.
- Кэш по `ad_id`; дневные лимиты.
- Промпт менять только вместе с тестами на классификацию:
  «штаны серебряного цвета» → skip; «браслет серебряный без веса» → ask;
  «кольцо 925, вес 10 г, 30 BYN» → look/below.
- Фото: 1–3 изображения, `gallery` для клейма/весов, `listings` для общего
  вида. Не хранить после анализа.

## Перед коммитом

```bash
.venv/bin/python -m pytest -q
git diff --check
git status --short
```

Коммит — короткий, по-русски, с указанием шага. Не коммитить `.env`, `data/`,
`logs/`, ключи.

## При истёкшем ключе

`401 api_key_expired` → AI/vision fallback. Не спамить лидами: обычные `ask`
без AI уходят в дайджест/скип, priority и below/suspicious работают. Сообщить
владельцу, что нужен новый ключ.
