"""Запрет ухода компьютера в сон.

Важно: это запрет именно idle-sleep. Если система уже ушла в сон (S3),
процесс приостанавливается вместе с ней. Для работы 24/7 нужен VPS или
wake timers (BIOS/RTC + планировщик), а не только этот модуль.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
SYSTEMD_INHIBIT_COMMAND = [
    "--what=sleep",
    "--who=SilverCoockold",
    "--why=monitoring",
    "sleep",
    "infinity",
]


class SleepInhibitor:
    """Кроссплатформенный запрет сна; повторные вызовы идемпотентны."""

    def __init__(self) -> None:
        self._enabled = False
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def enabled(self) -> bool:
        """Включён ли запрет сна."""
        return self._enabled

    def set_prevent_sleep(self, enabled: bool) -> None:
        """Включить или выключить запрет сна."""
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        try:
            if enabled:
                self._enable()
            else:
                self._disable()
        except Exception:
            logger.exception("Не удалось изменить режим сна")
        self._enabled = enabled

    def _enable(self) -> None:
        """Запретить сон средствами текущей ОС."""
        if sys.platform.startswith("win"):
            self._enable_windows()
            return

        command = shutil.which("systemd-inhibit")
        if command is None:
            logger.warning(
                "systemd-inhibit не найден: запрет сна недоступен на этой системе"
            )
            return

        self._process = subprocess.Popen(
            [command, *SYSTEMD_INHIBIT_COMMAND],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Запрет сна включён (systemd-inhibit)")

    def _disable(self) -> None:
        """Снять запрет сна."""
        if sys.platform.startswith("win"):
            self._disable_windows()
            return

        process = self._process
        self._process = None
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        logger.info("Запрет сна выключен")

    @staticmethod
    def _enable_windows() -> None:
        """Windows: ES_CONTINUOUS | ES_SYSTEM_REQUIRED из главного потока."""
        import ctypes

        result = ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        )
        if not result:
            logger.warning("SetThreadExecutionState вернул 0: запрет сна не применён")
        else:
            logger.info("Запрет сна включён (SetThreadExecutionState)")

    @staticmethod
    def _disable_windows() -> None:
        """Windows: вернуть ES_CONTINUOUS, разрешив сон."""
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)  # type: ignore[attr-defined]
        logger.info("Запрет сна выключен (SetThreadExecutionState)")

    def close(self) -> None:
        """Снять запрет сна при выходе."""
        self.set_prevent_sleep(False)
