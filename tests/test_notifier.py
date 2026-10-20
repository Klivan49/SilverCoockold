"""Тесты уведомлений Telegram: формат, экранирование и dry-run, без сети."""

from __future__ import annotations

import asyncio
import logging

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter

from app import notifier as notifier_module
from app.models import Ad
from app.notifier import Notifier, format_ad_message
from app.silver import SilverAnalysis


def make_ad(
    subject: str = "монеты серебро",
    price_byn: int | None = 2499,
    url: str = "https://www.kufar.by/item/1089730241",
) -> Ad:
    """Собрать тестовое объявление."""
    return Ad(
        ad_id="1089730241",
        subject=subject,
        price_byn=price_byn,
        url=url,
        list_time="2026-10-17T07:02:04Z",
        raw={"ad_id": "1089730241"},
    )


class FakeBot:
    """Подделка Bot: записывает вызовы send_message без сети."""

    def __init__(
        self, fail_with: Exception | None = None, fail_times: int = 0
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_with = fail_with
        self.fail_times = fail_times

    async def send_message(self, **kwargs: object) -> object:
        if self.fail_times > 0:
            self.fail_times -= 1
            assert self.fail_with is not None
            raise self.fail_with
        self.calls.append(kwargs)
        return object()


def test_price_format() -> None:
    """2499 копеек → 24,99 BYN, None и 0 → договорная."""
    assert "24,99 BYN" in format_ad_message(make_ad(price_byn=2499), "q")
    assert "Цена: договорная" in format_ad_message(make_ad(price_byn=None), "q")
    assert "Цена: договорная" in format_ad_message(make_ad(price_byn=0), "q")


def test_html_escaping() -> None:
    """Символы <, >, & экранируются в query, subject и url."""
    ad = make_ad(
        subject="Лот <серебро> & монеты",
        url="https://www.kufar.by/item/1?a=1&b=2",
    )

    text = format_ad_message(ad, "серебро & <лом>")

    assert "«серебро &amp; &lt;лом&gt;»" in text
    assert "Лот &lt;серебро&gt; &amp; монеты" in text
    assert "a=1&amp;b=2" in text
    assert "<серебро>" not in text
    assert "<лом>" not in text


def make_analysis(**kwargs: object) -> SilverAnalysis:
    """Разбор с разумными значениями по умолчанию."""
    values: dict[str, object] = {
        "weight_g": 14.4,
        "purity": 925,
        "effective_purity": 925,
        "price_byn": 115.06,
        "price_per_gram": 7.99,
        "scrap_price": 80.64,
        "is_below_scrap": False,
        "per_gram_pricing": False,
        "note": "",
    }
    values.update(kwargs)
    return SilverAnalysis(**values)  # type: ignore[arg-type]


def test_analysis_lines_in_message() -> None:
    """В сообщении есть вес, проба, цена за грамм и лом."""
    text = format_ad_message(
        make_ad(price_byn=11506), "серебро лом", make_analysis()
    )

    assert "⚖️ 14,4 г · 925" in text
    assert "💰 7,99 BYN/г · лом 5,60 BYN/г" in text
    assert "💰 НИЖЕ ЛОМА" not in text


def test_below_scrap_prefix() -> None:
    """Ниже лома — префикс перед текстом."""
    text = format_ad_message(
        make_ad(price_byn=1000), "серебро лом", make_analysis(is_below_scrap=True)
    )

    assert text.startswith("💰 НИЖЕ ЛОМА\n")
    assert "⚖️ 14,4 г · 925" in text


def test_lead_prefix_and_question() -> None:
    """Лид получает жёлтый префикс и вопрос продавцу."""
    analysis = make_analysis(
        weight_g=None,
        purity=None,
        effective_purity=None,
        price_byn=None,
        price_per_gram=None,
        scrap_price=None,
        is_below_scrap=False,
        is_lead=True,
    )

    text = format_ad_message(make_ad(price_byn=0), "серебро", analysis)

    assert text.startswith("🟡 ПРОВЕРИТЬ\n")
    assert "Цена: договорная" in text
    assert "Подскажите точный вес и есть ли клеймо?" in text
    assert "НИЖЕ ЛОМА" not in text


def test_per_gram_price_line() -> None:
    """Цена за грамм отображается как BYN/г (за грамм)."""
    analysis = make_analysis(
        weight_g=10.0,
        price_byn=20.0,
        price_per_gram=20.0,
        scrap_price=56.0,
        per_gram_pricing=True,
        is_below_scrap=True,
    )

    text = format_ad_message(make_ad(price_byn=2000), "серебро", analysis)

    assert "— Цена: 20 BYN/г (за грамм)" in text
    assert "20,00 BYN/г · лом 5,60 BYN/г" in text


def test_hallmark_fallback_in_message() -> None:
    """Показываем 925→800 при отсутствии клейма."""
    text = format_ad_message(
        make_ad(), "серебро", make_analysis(effective_purity=800)
    )

    assert "⚖️ 14,4 г · 925→800" in text


def test_long_subject_is_truncated() -> None:
    """Subject обрезается до 200 символов до экранирования."""
    ad = make_ad(subject="а" * 250)

    text = format_ad_message(ad, "q")
    subject_part = text.splitlines()[1].split(" — ")[0]

    assert len(subject_part) == 200
    assert "а" * 201 not in text


def test_dry_run_send_ad_logs_and_returns_true(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DRY_RUN пишет сообщение в лог, возвращает True и не создаёт Bot."""
    notifier = Notifier(token="123456:secret-token", chat_id="42", dry_run=True)

    with caplog.at_level(logging.INFO, logger="app.notifier"):
        result = asyncio.run(notifier.send_ad(make_ad(), "серебро лом"))

    assert result is True
    assert "[DRY_RUN]" in caplog.text
    assert "24,99 BYN" in caplog.text
    assert "123456:secret-token" not in caplog.text
    assert "\n" not in caplog.records[0].getMessage()
    assert notifier._bot is None
    asyncio.run(notifier.close())


def test_without_token_uses_dry_run(caplog: pytest.LogCaptureFixture) -> None:
    """Без токена и chat_id отправка не идёт в сеть, даже если DRY_RUN=0."""
    notifier = Notifier(token=None, chat_id=None, dry_run=False)

    with caplog.at_level(logging.INFO, logger="app.notifier"):
        result = asyncio.run(notifier.send_text("тест"))

    assert result is True
    assert "[DRY_RUN]" in caplog.text
    assert notifier._bot is None


def test_send_text_uses_html_and_no_preview() -> None:
    """Реальная отправка идёт с parse_mode=HTML и без превью."""
    notifier = Notifier(token="123456:secret-token", chat_id="42", dry_run=False)
    fake = FakeBot()
    notifier._bot = fake  # type: ignore[assignment]

    result = asyncio.run(notifier.send_text("<b>текст</b>"))

    assert result is True
    assert fake.calls[0]["chat_id"] == "42"
    assert fake.calls[0]["text"] == "<b>текст</b>"
    assert fake.calls[0]["parse_mode"] == "HTML"
    assert fake.calls[0]["disable_web_page_preview"] is True
    assert fake.calls[0]["reply_markup"] is None


def test_send_ad_has_scan_keyboard() -> None:
    """Каждый алерт уходит с inline-кнопкой «🔄 Скан»."""
    notifier = Notifier(token="123456:secret-token", chat_id="42", dry_run=False)
    fake = FakeBot()
    notifier._bot = fake  # type: ignore[assignment]

    result = asyncio.run(notifier.send_ad(make_ad(), "серебро лом"))

    assert result is True
    assert fake.calls[0]["text"].startswith("🔥 Новое: «серебро лом»")
    markup = fake.calls[0]["reply_markup"]
    button = markup.inline_keyboard[0][0]
    assert button.text == "🔄 Скан"
    assert button.callback_data == "force_scan"


def test_retry_after_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """TelegramRetryAfter: пауза retry_after и одна повторная попытка."""
    notifier = Notifier(token="123456:secret-token", chat_id="42", dry_run=False)
    fake = FakeBot(
        fail_with=TelegramRetryAfter(None, "Too Many Requests", 3),  # type: ignore[arg-type]
        fail_times=1,
    )
    notifier._bot = fake  # type: ignore[assignment]
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(notifier_module.asyncio, "sleep", fake_sleep)

    result = asyncio.run(notifier.send_text("тест"))

    assert result is True
    assert delays == [3]
    assert len(fake.calls) == 1


def test_network_error_returns_false_and_hides_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Сетевая ошибка не роняет процесс, токен в лог не попадает."""
    notifier = Notifier(token="123456:secret-token", chat_id="42", dry_run=False)
    fake = FakeBot(
        fail_with=TelegramNetworkError(
            None,  # type: ignore[arg-type]
            "request to https://api.telegram.org/bot123456:secret-token/sendMessage failed",
        ),
        fail_times=1,
    )
    notifier._bot = fake  # type: ignore[assignment]

    with caplog.at_level(logging.ERROR, logger="app.notifier"):
        result = asyncio.run(notifier.send_text("тест"))

    assert result is False
    assert "chat_id=42" in caplog.text
    assert "123456:secret-token" not in caplog.text
    assert "bot***" in caplog.text


class ChatFailBot:
    """Подделка Bot, падающая только для одного chat_id."""

    def __init__(self, fail_chat: str) -> None:
        self.fail_chat = fail_chat
        self.calls: list[str] = []

    async def send_message(self, **kwargs: object) -> object:
        chat_id = str(kwargs["chat_id"])
        if chat_id == self.fail_chat:
            raise TelegramNetworkError(None, "chat is unavailable")  # type: ignore[arg-type]
        self.calls.append(chat_id)
        return object()


def test_multiple_recipients_one_failure_does_not_block_others() -> None:
    """Ошибка одного получателя не мешает отправить остальным."""
    notifier = Notifier(token="123456:secret-token", chat_ids=["1", "2", "3"], dry_run=False)
    fake = ChatFailBot(fail_chat="2")
    notifier._bot = fake  # type: ignore[assignment]

    result = asyncio.run(notifier.send_text("тест"))

    assert result is False
    assert fake.calls == ["1", "3"]


def test_multiple_recipients_all_success() -> None:
    """Успешная отправка возвращает True для всех получателей."""
    notifier = Notifier(token="123456:secret-token", chat_ids=["1", "2"], dry_run=False)
    fake = ChatFailBot(fail_chat="none")
    notifier._bot = fake  # type: ignore[assignment]

    assert asyncio.run(notifier.send_text("тест")) is True
    assert fake.calls == ["1", "2"]


def test_close_without_bot_is_safe() -> None:
    """close() без созданного Bot ничего не делает."""
    notifier = Notifier(token=None, chat_id=None, dry_run=True)

    asyncio.run(notifier.close())
