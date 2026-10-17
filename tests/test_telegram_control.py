"""Тесты TelegramPoller: /start, кнопка, кулдаун и чужой чат (без сети)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from app.models import Ad
from app.storage import Storage
from app.telegram_control import (
    COMPLETE_TEXT,
    LAST_FORCE_SCAN_KEY,
    START_TEXT,
    TelegramPoller,
)


class FakeBot:
    """Подделка Bot: getUpdates, sendMessage, answerCallbackQuery."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []
        self.updates_calls: list[dict[str, Any]] = []

    async def get_updates(self, **kwargs: Any) -> list[object]:
        self.updates_calls.append(kwargs)
        return []

    async def send_message(self, **kwargs: Any) -> object:
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=len(self.sent))

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
        **kwargs: Any,
    ) -> bool:
        self.answers.append(
            {
                "id": callback_query_id,
                "text": text,
                "show_alert": show_alert,
            }
        )
        return True


class FakeScheduler:
    """Подделка планировщика с управляемой задержкой скана."""

    def __init__(self, storage: Storage, delay: float = 0.0) -> None:
        self.storage = storage
        self.delay = delay
        self.calls = 0
        self._running = False
        self.tracking = True
        self.prevent_sleep = False
        self.config = SimpleNamespace(
            queries=[SimpleNamespace(q="серебро лом")]
        )

    @property
    def is_manual_scan_running(self) -> bool:
        return self._running

    async def run_manual_scan(self) -> dict[str, int]:
        self.calls += 1
        self._running = True
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return {"new": 1, "sent": 2}
        finally:
            self._running = False


def message_update(
    chat_id: int,
    text: str,
    update_id: int = 1,
    user_id: int | None = None,
) -> Any:
    """Собрать update с сообщением."""
    return SimpleNamespace(
        update_id=update_id,
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            from_user=SimpleNamespace(id=user_id if user_id is not None else chat_id),
            text=text,
        ),
        callback_query=None,
    )


def callback_update(
    chat_id: int,
    data: str = "force_scan",
    callback_id: str = "cb1",
    update_id: int = 2,
    user_id: int | None = None,
) -> Any:
    """Собрать update с callback_query."""
    return SimpleNamespace(
        update_id=update_id,
        message=None,
        callback_query=SimpleNamespace(
            id=callback_id,
            data=data,
            from_user=SimpleNamespace(id=user_id if user_id is not None else chat_id),
            message=SimpleNamespace(chat=SimpleNamespace(id=chat_id)),
        ),
    )


def make_poller(
    storage: Storage,
    scheduler: FakeScheduler,
    bot: FakeBot,
    cooldown: int = 300,
    chat_ids: list[str] | None = None,
    admin_ids: list[str] | None = None,
) -> TelegramPoller:
    """Собрать poller на подделках."""
    return TelegramPoller(
        token="123456:test-token",
        chat_ids=chat_ids or ["42"],
        admin_ids=admin_ids or ["42"],
        scheduler=scheduler,  # type: ignore[arg-type]
        storage=storage,
        cooldown_sec=cooldown,
        bot=bot,  # type: ignore[arg-type]
    )


def test_start_sends_keyboard(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """/start отправляет сообщение с inline-кнопкой."""
    storage = Storage(tmp_path / "test.db")
    bot = FakeBot()
    poller = make_poller(storage, FakeScheduler(storage), bot)

    asyncio.run(poller.handle_update(message_update(42, "/start")))

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == START_TEXT
    markup = bot.sent[0]["reply_markup"]
    button = markup.inline_keyboard[0][0]
    assert button.text == "🔄 Принудительный скан"
    assert button.callback_data == "force_scan"


def test_scan_command_sends_keyboard(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """/scan тоже показывает кнопку."""
    storage = Storage(tmp_path / "test.db")
    bot = FakeBot()
    poller = make_poller(storage, FakeScheduler(storage), bot)

    asyncio.run(poller.handle_update(message_update(42, "/scan")))

    assert len(bot.sent) == 1
    assert bot.sent[0]["reply_markup"] is not None


def test_id_answers_any_chat(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """/id доступен любому чату и возвращает chat_id/user_id."""
    storage = Storage(tmp_path / "test.db")
    bot = FakeBot()
    poller = make_poller(storage, FakeScheduler(storage), bot)

    asyncio.run(poller.handle_update(message_update(999, "/id", user_id=555)))

    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == 999
    assert bot.sent[0]["text"] == "chat_id=999, user_id=555"


def test_status_for_admin(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """/status показывает состояние и последнюю ошибку Kufar."""
    storage = Storage(tmp_path / "test.db")
    storage.save(make_ad_for_status(), "серебро лом")
    storage.set_last_poll("серебро лом", "2026-10-17T10:00:00+00:00")
    storage.set_meta("kufar_error_active", "1")
    storage.set_meta("kufar_error_kind", "rate_limit")
    storage.set_meta("kufar_error_query", "серебро лом")
    storage.set_meta("kufar_error_status", "429")
    bot = FakeBot()
    scheduler = FakeScheduler(storage)
    poller = make_poller(storage, scheduler, bot)

    asyncio.run(poller.handle_update(message_update(42, "/status", user_id=42)))

    text = bot.sent[-1]["text"]
    assert "Отслеживание: включено" in text
    assert "Объявлений в базе: 1" in text
    assert "rate_limit" in text and "429" in text
    assert "Кулдаун скана: готов" in text


def test_status_rejected_for_non_admin(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Чужой /status молча игнорируется."""
    storage = Storage(tmp_path / "test.db")
    bot = FakeBot()
    poller = make_poller(storage, FakeScheduler(storage), bot)

    asyncio.run(poller.handle_update(message_update(999, "/status", user_id=555)))

    assert bot.sent == []


def make_ad_for_status() -> Ad:
    """Объявление для проверки ads_total."""
    return Ad(
        ad_id="1",
        subject="серебро",
        price_byn=100,
        url="https://www.kufar.by/item/1",
        list_time=None,
        raw={},
    )


def test_non_admin_start_gets_onboarding(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Чужой /start получает свои ID, но не подписывается и не видит кнопку."""
    storage = Storage(tmp_path / "test.db")
    bot = FakeBot()
    scheduler = FakeScheduler(storage)
    poller = make_poller(storage, scheduler, bot)

    asyncio.run(poller.handle_update(message_update(999, "/start", user_id=555)))
    asyncio.run(poller.handle_update(message_update(999, "/scan", user_id=555)))
    asyncio.run(poller.handle_update(callback_update(999, user_id=555)))

    assert len(bot.sent) == 1
    assert "Ваш chat_id=999, user_id=555" in bot.sent[0]["text"]
    assert "Передайте" in bot.sent[0]["text"]
    assert "reply_markup" not in bot.sent[0]
    assert bot.answers == []
    assert scheduler.calls == 0


def test_admin_callback_from_foreign_chat(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Админ может нажать кнопку в чужом чате; итог уходит в этот чат."""
    storage = Storage(tmp_path / "test.db")
    bot = FakeBot()
    scheduler = FakeScheduler(storage)
    poller = make_poller(
        storage, scheduler, bot, chat_ids=["42"], admin_ids=["42"]
    )

    async def scenario() -> None:
        await poller.handle_update(
            callback_update(999, user_id=42, callback_id="cb-foreign")
        )
        assert poller._scan_task is not None
        await poller._scan_task

    asyncio.run(scenario())

    assert scheduler.calls == 1
    assert bot.answers[-1]["id"] == "cb-foreign"
    assert bot.sent[-1]["chat_id"] == 999
    assert bot.sent[-1]["text"] == COMPLETE_TEXT.format(new=1, sent=2)


def test_non_admin_callback_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Пользователь вне admin_ids не запускает скан даже из чата админа."""
    storage = Storage(tmp_path / "test.db")
    bot = FakeBot()
    scheduler = FakeScheduler(storage)
    poller = make_poller(storage, scheduler, bot)

    asyncio.run(poller.handle_update(callback_update(42, user_id=999)))

    assert scheduler.calls == 0
    assert bot.answers == []
    assert poller._scan_task is None


def test_force_scan_runs_once(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Callback запускает скан один раз и присылает итог."""
    storage = Storage(tmp_path / "test.db")
    bot = FakeBot()
    scheduler = FakeScheduler(storage)
    poller = make_poller(storage, scheduler, bot)

    async def scenario() -> None:
        await poller.handle_update(callback_update(42))
        assert poller._scan_task is not None
        await poller._scan_task

    asyncio.run(scenario())

    assert scheduler.calls == 1
    assert bot.answers == [
        {"id": "cb1", "text": "Скан запущен", "show_alert": False}
    ]
    assert bot.sent[-1]["text"] == COMPLETE_TEXT.format(new=1, sent=2)


def test_cooldown_blocks_repeat_callback(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Повторный callback в кулдаун не запускает скан и показывает alert."""
    storage = Storage(tmp_path / "test.db")
    bot = FakeBot()
    scheduler = FakeScheduler(storage)
    poller = make_poller(storage, scheduler, bot)

    async def scenario() -> None:
        await poller.handle_update(callback_update(42, callback_id="cb1"))
        assert poller._scan_task is not None
        await poller._scan_task
        await poller.handle_update(callback_update(42, callback_id="cb2"))

    asyncio.run(scenario())

    assert scheduler.calls == 1
    assert bot.answers[-1]["show_alert"] is True
    assert bot.answers[-1]["text"].startswith("Подождите ")
    assert bot.answers[-1]["text"].endswith(" с")


def test_parallel_scan_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Пока скан идёт, второй callback получает отказ."""
    storage = Storage(tmp_path / "test.db")
    bot = FakeBot()
    scheduler = FakeScheduler(storage, delay=0.3)
    poller = make_poller(storage, scheduler, bot)

    async def scenario() -> None:
        await poller.handle_update(callback_update(42, callback_id="cb1"))
        await asyncio.sleep(0)
        await poller.handle_update(callback_update(42, callback_id="cb2"))
        assert poller._scan_task is not None
        await poller._scan_task

    asyncio.run(scenario())

    assert scheduler.calls == 1
    assert bot.answers[-1] == {
        "id": "cb2",
        "text": "Скан уже выполняется",
        "show_alert": True,
    }


def test_scan_allowed_after_cooldown(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """После истечения кулдауна скан снова запускается."""
    storage = Storage(tmp_path / "test.db")
    old = (datetime.now(timezone.utc) - timedelta(seconds=301)).isoformat()
    storage.set_meta(LAST_FORCE_SCAN_KEY, old)
    bot = FakeBot()
    scheduler = FakeScheduler(storage)
    poller = make_poller(storage, scheduler, bot)

    async def scenario() -> None:
        await poller.handle_update(callback_update(42))
        assert poller._scan_task is not None
        await poller._scan_task

    asyncio.run(scenario())

    assert scheduler.calls == 1


def test_cooldown_persists_between_storage_instances(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Кулдаун хранится в meta и виден новому Storage/Poller."""
    db_path = tmp_path / "test.db"
    first_storage = Storage(db_path)
    first_bot = FakeBot()
    first_scheduler = FakeScheduler(first_storage)
    first = make_poller(first_storage, first_scheduler, first_bot)

    async def first_scan() -> None:
        await first.handle_update(callback_update(42))
        assert first._scan_task is not None
        await first._scan_task

    asyncio.run(first_scan())

    second_storage = Storage(db_path)
    second_bot = FakeBot()
    second_scheduler = FakeScheduler(second_storage)
    second = make_poller(second_storage, second_scheduler, second_bot)

    asyncio.run(second.handle_update(callback_update(42)))

    assert second_scheduler.calls == 0
    assert second_bot.answers[-1]["show_alert"] is True
