"""Уведомления в Telegram на aiogram 3 с режимом DRY_RUN."""

from __future__ import annotations

import asyncio
import html
import logging
import re

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

from app.models import Ad

logger = logging.getLogger(__name__)

SUBJECT_MAX_LENGTH = 200
TOKEN_PATTERN = re.compile(r"bot\d+:[A-Za-z0-9_-]+")


def format_ad_message(ad: Ad, query: str) -> str:
    """Собрать HTML-сообщение о новом объявлении."""
    safe_query = html.escape(query, quote=False)
    safe_subject = html.escape((ad.subject or "")[:SUBJECT_MAX_LENGTH], quote=False)
    safe_url = html.escape(ad.url, quote=False)

    if ad.price_byn and ad.price_byn > 0:
        price = f"{ad.price_byn / 100:.2f}".replace(".", ",")
        price_text = f"{price} BYN"
    else:
        price_text = "Цена: договорная"

    return (
        f"🔥 Новое: «{safe_query}»\n"
        f"{safe_subject} — {price_text}\n"
        f"{safe_url}"
    )


def safe_error(exc: BaseException, token: str | None) -> str:
    """Описание ошибки без токена бота."""
    message = str(exc)
    if token:
        message = message.replace(token, "***")
    message = TOKEN_PATTERN.sub("bot***", message)
    return f"{type(exc).__name__}: {message}"


class Notifier:
    """Отправка сообщений в Telegram или запись в лог при DRY_RUN."""

    def __init__(self, token: str | None, chat_id: str | None, dry_run: bool) -> None:
        """Запомнить настройки; Bot создаётся только при первой отправке."""
        self._token = token
        self._chat_id = chat_id
        self._dry_run = dry_run
        self._bot: Bot | None = None

    def _is_dry_run(self) -> bool:
        """Dry-run включается флагом или отсутствием токена/chat_id."""
        return self._dry_run or not self._token or not self._chat_id

    def _get_bot(self) -> Bot:
        """Лениво создать Bot при первой реальной отправке."""
        if self._bot is None:
            if not self._token:
                raise RuntimeError("Telegram token не задан")
            self._bot = Bot(token=self._token)
        return self._bot

    async def send_ad(self, ad: Ad, query: str) -> bool:
        """Отправить уведомление о новом объявлении."""
        return await self.send_text(format_ad_message(ad, query))

    async def send_text(self, text: str) -> bool:
        """Отправить готовое HTML-сообщение; вернуть True при успехе."""
        if self._is_dry_run():
            logger.info("[DRY_RUN] %s", " ".join(text.splitlines()))
            return True

        bot = self._get_bot()
        try:
            await self._send(bot, text)
            return True
        except TelegramRetryAfter as exc:
            logger.warning(
                "Telegram: flood control, повтор через %s с", exc.retry_after
            )
            await asyncio.sleep(exc.retry_after)
        except (TelegramAPIError, OSError) as exc:
            logger.error(
                "Telegram: отправка не удалась: %s", safe_error(exc, self._token)
            )
            return False

        try:
            await self._send(bot, text)
            return True
        except (TelegramAPIError, OSError) as exc:
            logger.error(
                "Telegram: повторная отправка не удалась: %s",
                safe_error(exc, self._token),
            )
            return False

    async def _send(self, bot: Bot, text: str) -> None:
        """Вызвать sendMessage с HTML и без превью ссылки."""
        await bot.send_message(
            chat_id=self._chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    async def close(self) -> None:
        """Закрыть сессию бота, если она была создана."""
        if self._bot is None:
            return
        try:
            await self._bot.session.close()
        finally:
            self._bot = None
