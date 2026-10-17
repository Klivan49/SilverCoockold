"""Long polling Telegram: /start, /scan и кнопка принудительного скана."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)

from app.notifier import safe_error
from app.scheduler import Scheduler
from app.storage import Storage, utc_now_iso

logger = logging.getLogger(__name__)

CALLBACK_FORCE_SCAN = "force_scan"
LAST_FORCE_SCAN_KEY = "last_force_scan_at"
FORCE_SCAN_BUTTON_TEXT = "🔄 Принудительный скан"
START_TEXT = "SilverCoockold на связи. Нажмите кнопку, чтобы запустить скан."
COMPLETE_TEXT = "Скан завершён: новых {new}, отправлено {sent}"
ONBOARD_TEXT = (
    "Ваш chat_id={chat_id}, user_id={user_id}. "
    "Передайте эти данные владельцу — он добавит вас в списки и перезапустит бота."
)


def _normalize_ids(values: list[str] | str | None) -> list[str]:
    """Привести список/строку ID к списку без пустых и повторов."""
    if values is None:
        return []
    raw = [values] if isinstance(values, str) else list(values)
    result: list[str] = []
    for value in raw:
        normalized = str(value).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def force_scan_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для /start и /scan."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=FORCE_SCAN_BUTTON_TEXT,
                    callback_data=CALLBACK_FORCE_SCAN,
                )
            ]
        ]
    )


class TelegramPoller:
    """Long polling getUpdates и обработка кнопки скана."""

    def __init__(
        self,
        token: str | None,
        chat_ids: list[str] | str | None = None,
        scheduler: Scheduler | None = None,
        storage: Storage | None = None,
        cooldown_sec: int = 300,
        bot: Bot | None = None,
        *,
        chat_id: str | None = None,
        admin_ids: list[str] | str | None = None,
    ) -> None:
        self._token = token
        self._chat_ids = _normalize_ids(chat_ids)
        if chat_id is not None:
            self._chat_ids = _normalize_ids([*self._chat_ids, chat_id])
        self._admin_ids = _normalize_ids(admin_ids) or list(self._chat_ids)
        if scheduler is None or storage is None:
            raise ValueError("scheduler и storage обязательны")
        self.scheduler = scheduler
        self.storage = storage
        self.cooldown_sec = cooldown_sec
        self._owns_bot = bot is None
        self._bot = bot
        self._offset = 0
        self._scan_task: asyncio.Task[None] | None = None

    def _get_bot(self) -> Bot:
        """Лениво создать Bot для поллинга."""
        if self._bot is None:
            if not self._token:
                raise RuntimeError("Telegram token не задан")
            self._bot = Bot(token=self._token)
        return self._bot

    def _is_admin(self, user_id: object) -> bool:
        """Разрешён ли пользователь (по user_id, не по chat_id)."""
        return user_id is not None and str(user_id) in self._admin_ids

    def _cooldown_left(self) -> int:
        """Сколько секунд осталось до конца кулдауна."""
        last = self.storage.get_meta(LAST_FORCE_SCAN_KEY)
        if not last:
            return 0
        try:
            last_dt = datetime.fromisoformat(last)
        except ValueError:
            return 0
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
        remaining = self.cooldown_sec - elapsed
        if remaining <= 0:
            return 0
        return max(1, min(self.cooldown_sec, math.ceil(remaining)))

    def _scan_running(self) -> bool:
        """Идёт ли скан (в планировщике или в фоновой задаче поллера)."""
        if self.scheduler.is_manual_scan_running:
            return True
        return self._scan_task is not None and not self._scan_task.done()

    async def run(self) -> None:
        """Бесконечный long polling."""
        if not self._token:
            logger.info("Telegram poller не запущен: нет токена")
            return

        bot = self._get_bot()
        try:
            stale = await bot.get_updates(offset=-1, limit=1)
            self._offset = stale[-1].update_id + 1 if stale else 0
        except Exception as exc:
            logger.error(
                "Telegram poller: не удалось пропустить старые updates: %s",
                safe_error(exc, self._token),
            )
            self._offset = 0

        logger.info("Telegram poller запущен")
        while True:
            try:
                updates = await bot.get_updates(
                    offset=self._offset,
                    timeout=25,
                    allowed_updates=["message", "callback_query"],
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Telegram poller: ошибка getUpdates: %s",
                    safe_error(exc, self._token),
                )
                await asyncio.sleep(5)
                continue

            for update in updates:
                self._offset = update.update_id + 1
                try:
                    await self.handle_update(update)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error(
                        "Telegram poller: ошибка обработки update: %s",
                        safe_error(exc, self._token),
                    )
                    await asyncio.sleep(5)

    async def handle_update(self, update: Update) -> None:
        """Обработать одно обновление."""
        message = getattr(update, "message", None)
        callback = getattr(update, "callback_query", None)
        if message is not None:
            await self._handle_message(message)
        elif callback is not None:
            await self._handle_callback(callback)

    async def _handle_message(self, message: Message) -> None:
        """/id — всем, /start и /scan — только админам."""
        text = message.text or ""
        if not text:
            return
        command = text.split()[0].split("@")[0]
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user is not None else None
        bot = self._get_bot()

        if command == "/id":
            await bot.send_message(
                chat_id=chat_id,
                text=f"chat_id={chat_id}, user_id={user_id if user_id is not None else '—'}",
            )
            return

        if command not in {"/start", "/scan"}:
            return

        if not self._is_admin(user_id):
            if command == "/start":
                await bot.send_message(
                    chat_id=chat_id,
                    text=ONBOARD_TEXT.format(
                        chat_id=chat_id,
                        user_id=user_id if user_id is not None else "—",
                    ),
                )
            return

        await bot.send_message(
            chat_id=chat_id,
            text=START_TEXT,
            reply_markup=force_scan_keyboard(),
        )

    async def _handle_callback(self, callback: CallbackQuery) -> None:
        """Кнопка force_scan: проверка from_user.id, кулдаун и запуск."""
        if callback.data != CALLBACK_FORCE_SCAN:
            return
        user_id = (
            callback.from_user.id
            if callback.from_user is not None
            else None
        )
        if not self._is_admin(user_id):
            return

        message = callback.message
        chat_id = message.chat.id if message is not None else user_id
        bot = self._get_bot()

        if self._scan_running():
            await bot.answer_callback_query(
                callback.id,
                text="Скан уже выполняется",
                show_alert=True,
            )
            return

        remaining = self._cooldown_left()
        if remaining > 0:
            await bot.answer_callback_query(
                callback.id,
                text=f"Подождите {remaining} с",
                show_alert=True,
            )
            return

        self.storage.set_meta(LAST_FORCE_SCAN_KEY, utc_now_iso())
        await bot.answer_callback_query(callback.id, text="Скан запущен")
        self._scan_task = asyncio.create_task(
            self._run_scan(bot, chat_id), name="force-scan"
        )

    async def _run_scan(self, bot: Bot, chat_id: object) -> None:
        """Выполнить ручной скан и отправить итог в чат нажатия."""
        try:
            result = await self.scheduler.run_manual_scan()
            await bot.send_message(
                chat_id=chat_id,
                text=COMPLETE_TEXT.format(
                    new=result.get("new", 0), sent=result.get("sent", 0)
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Ручной скан завершился ошибкой: %s",
                safe_error(exc, self._token),
            )
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text="Скан завершился с ошибкой, подробности в логе.",
                )
            except Exception as send_exc:
                logger.error(
                    "Не удалось сообщить об ошибке скана: %s",
                    safe_error(send_exc, self._token),
                )
        finally:
            self._scan_task = None

    async def close(self) -> None:
        """Отменить незавершённый скан и закрыть сессию своего Bot."""
        if self._scan_task is not None and not self._scan_task.done():
            self._scan_task.cancel()
            await asyncio.gather(self._scan_task, return_exceptions=True)
        self._scan_task = None
        if self._owns_bot and self._bot is not None:
            await self._bot.session.close()
            self._bot = None
