"""Отправить алерт во все чаты при падении/зависании сервиса."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import ConfigError, load_config
from app.notifier import Notifier

logger = logging.getLogger(__name__)

ALERT_TEXT = "⚠️ SilverCoockold: сервис упал или завис"


async def send_alert() -> bool:
    """Отправить сообщение всем настроенным получателям."""
    try:
        config = load_config()
    except ConfigError as exc:
        logger.error("Не удалось загрузить конфиг: %s", exc)
        return False

    if not config.telegram_token or not config.telegram_chat_ids:
        logger.error("Нет TELEGRAM_BOT_TOKEN или получателей для алерта")
        return False

    notifier = Notifier(
        config.telegram_token, config.telegram_chat_ids, config.dry_run
    )
    try:
        return await notifier.send_text(ALERT_TEXT)
    finally:
        await notifier.close()


def main() -> int:
    """Точка входа для systemd OnFailure."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        success = asyncio.run(send_alert())
    except Exception:
        logger.exception("Не удалось отправить алерт о падении")
        return 1
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
