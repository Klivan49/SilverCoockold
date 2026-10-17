"""systemd notify: READY=1 и WATCHDOG=1 через $NOTIFY_SOCKET."""

from __future__ import annotations

import asyncio
import logging
import os
import socket as socket_module

logger = logging.getLogger(__name__)

WATCHDOG_INTERVAL_SECONDS = 30


class SystemdNotifier:
    """Отправка sd_notify-сообщений; без $NOTIFY_SOCKET — no-op."""

    def __init__(self) -> None:
        self.address = os.getenv("NOTIFY_SOCKET")

    def notify(self, message: str) -> bool:
        """Отправить одно сообщение systemd."""
        if not self.address:
            return False
        address = (
            "\0" + self.address[1:]
            if self.address.startswith("@")
            else self.address
        )
        try:
            with socket_module.socket(
                socket_module.AF_UNIX, socket_module.SOCK_DGRAM
            ) as sock:
                sock.connect(address)
                sock.sendall(message.encode("utf-8"))
            return True
        except OSError:
            logger.warning("systemd-notify: не удалось отправить %s", message)
            return False

    async def run(self) -> None:
        """Отправить READY и затем WATCHDOG каждые 30 секунд."""
        if not self.address:
            logger.debug("systemd-notify: NOTIFY_SOCKET не задан, no-op")
            return
        self.notify("READY=1")
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
            self.notify("WATCHDOG=1")
