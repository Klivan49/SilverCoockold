"""Уведомления в Telegram на aiogram 3 с режимом DRY_RUN."""

from __future__ import annotations

import asyncio
import html
import logging
import re

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Ad
from app.silver import SilverAnalysis

logger = logging.getLogger(__name__)

SUBJECT_MAX_LENGTH = 200
TOKEN_PATTERN = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
SCAN_BUTTON_TEXT = "🔄 Скан"
FORCE_SCAN_CALLBACK = "force_scan"


def scan_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой принудительного скана для алертов."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=SCAN_BUTTON_TEXT, callback_data=FORCE_SCAN_CALLBACK
                )
            ]
        ]
    )


def _format_number(value: float) -> str:
    """Число без лишних нулей, с запятой."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _format_money(value: float) -> str:
    """Деньги: два знака после запятой."""
    return f"{value:.2f}".replace(".", ",")


def format_ad_message(
    ad: Ad, query: str, analysis: SilverAnalysis | None = None
) -> str:
    """Собрать HTML-сообщение о новом объявлении."""
    safe_query = html.escape(query, quote=False)
    safe_subject = html.escape((ad.subject or "")[:SUBJECT_MAX_LENGTH], quote=False)
    safe_url = html.escape(ad.url, quote=False)

    if analysis is not None and analysis.price_byn is not None:
        if analysis.per_gram_pricing:
            price_text = (
                f"Цена: {_format_number(analysis.price_byn)} BYN/г (за грамм)"
            )
        else:
            price_text = f"{_format_money(analysis.price_byn)} BYN"
    elif ad.price_byn and ad.price_byn > 0:
        price = f"{ad.price_byn / 100:.2f}".replace(".", ",")
        price_text = f"{price} BYN"
    else:
        price_text = "Цена: договорная"

    lines = [
        f"🔥 Новое: «{safe_query}»",
        f"{safe_subject} — {price_text}",
    ]
    if analysis is not None and analysis.weight_g is not None:
        purity_text = "—"
        if analysis.purity is not None:
            purity_text = str(analysis.purity)
            if (
                analysis.effective_purity is not None
                and analysis.effective_purity != analysis.purity
            ):
                purity_text += f"→{analysis.effective_purity}"
        lines.append(
            f"⚖️ {_format_number(analysis.weight_g)} г · {purity_text}"
        )
    if (
        analysis is not None
        and analysis.price_per_gram is not None
        and analysis.scrap_price is not None
        and analysis.weight_g
    ):
        scrap_per_gram = analysis.scrap_price / analysis.weight_g
        lines.append(
            f"💰 {_format_money(analysis.price_per_gram)} BYN/г · "
            f"лом {_format_money(scrap_per_gram)} BYN/г"
        )
    if analysis is not None and analysis.is_suspicious:
        lines.append("⚠️ Похоже на ошибку в цене/весе — проверьте")
    lines.append(safe_url)

    text = "\n".join(lines)
    if analysis is not None and analysis.is_suspicious:
        text = "⚠️ ПРОВЕРИТЬ\n" + text
    elif analysis is not None and analysis.is_below_scrap:
        text = "💰 НИЖЕ ЛОМА\n" + text
    return text


def safe_error(exc: BaseException, token: str | None) -> str:
    """Описание ошибки без токена бота."""
    message = str(exc)
    if token:
        message = message.replace(token, "***")
    message = TOKEN_PATTERN.sub("bot***", message)
    return f"{type(exc).__name__}: {message}"


class Notifier:
    """Отправка сообщений в Telegram или запись в лог при DRY_RUN."""

    def __init__(
        self,
        token: str | None,
        chat_ids: list[str] | str | None = None,
        dry_run: bool = True,
        *,
        chat_id: str | None = None,
    ) -> None:
        """Запомнить настройки; Bot создаётся только при первой отправке."""
        self._token = token
        raw_ids: list[str] = []
        if isinstance(chat_ids, str):
            raw_ids.append(chat_ids)
        elif chat_ids:
            raw_ids.extend(str(value) for value in chat_ids)
        if chat_id is not None:
            raw_ids.append(str(chat_id))
        self._chat_ids: list[str] = []
        for value in raw_ids:
            normalized = value.strip()
            if normalized and normalized not in self._chat_ids:
                self._chat_ids.append(normalized)
        self._chat_id = self._chat_ids[0] if self._chat_ids else None
        self._dry_run = dry_run
        self._bot: Bot | None = None

    def _is_dry_run(self) -> bool:
        """Dry-run включается флагом или отсутствием токена/получателей."""
        return self._dry_run or not self._token or not self._chat_ids

    def _get_bot(self) -> Bot:
        """Лениво создать Bot при первой реальной отправке."""
        if self._bot is None:
            if not self._token:
                raise RuntimeError("Telegram token не задан")
            self._bot = Bot(token=self._token)
        return self._bot

    async def send_ad(
        self, ad: Ad, query: str, analysis: SilverAnalysis | None = None
    ) -> bool:
        """Отправить уведомление о новом объявлении с кнопкой скана."""
        return await self.send_text(
            format_ad_message(ad, query, analysis),
            reply_markup=scan_keyboard(),
        )

    async def send_text(
        self, text: str, reply_markup: InlineKeyboardMarkup | None = None
    ) -> bool:
        """Отправить готовое HTML-сообщение; вернуть True при успехе."""
        if self._is_dry_run():
            logger.info("[DRY_RUN] %s", " ".join(text.splitlines()))
            return True

        bot = self._get_bot()
        results = []
        for chat_id in self._chat_ids:
            results.append(await self._send_to(bot, chat_id, text, reply_markup))
        return all(results)

    async def _send_to(
        self,
        bot: Bot,
        chat_id: str,
        text: str,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> bool:
        """Отправить одному получателю; ошибка не мешает остальным."""
        try:
            await self._send(bot, chat_id, text, reply_markup)
            return True
        except TelegramRetryAfter as exc:
            logger.warning(
                "Telegram: flood control для chat_id=%s, повтор через %s с",
                chat_id,
                exc.retry_after,
            )
            await asyncio.sleep(exc.retry_after)
        except (TelegramAPIError, OSError) as exc:
            logger.error(
                "Telegram: отправка в chat_id=%s не удалась: %s",
                chat_id,
                safe_error(exc, self._token),
            )
            return False

        try:
            await self._send(bot, chat_id, text, reply_markup)
            return True
        except (TelegramAPIError, OSError) as exc:
            logger.error(
                "Telegram: повторная отправка в chat_id=%s не удалась: %s",
                chat_id,
                safe_error(exc, self._token),
            )
            return False

    async def _send(
        self,
        bot: Bot,
        chat_id: str,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        """Вызвать sendMessage с HTML, без превью ссылки и с клавиатурой."""
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )

    async def close(self) -> None:
        """Закрыть сессию бота, если она была создана."""
        if self._bot is None:
            return
        try:
            await self._bot.session.close()
        finally:
            self._bot = None
