"""Тесты обработчиков сигналов: Linux и Windows-фолбэк."""

from __future__ import annotations

import asyncio
import signal

from app.main import install_signal_handlers


class FakeLoop:
    """Минимальная замена event loop для проверки установки обработчиков."""

    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.handlers: list[int] = []

    def add_signal_handler(self, sig: int, callback) -> None:
        if self.fail:
            raise NotImplementedError
        self.handlers.append(sig)

    def call_soon_threadsafe(self, callback) -> None:
        callback()


def test_install_signal_handlers_uses_loop() -> None:
    """На Unix обработчики ставятся через loop.add_signal_handler."""
    loop = FakeLoop(fail=False)
    stop = asyncio.Event()

    install_signal_handlers(loop, stop)  # type: ignore[arg-type]

    assert loop.handlers == [signal.SIGINT, signal.SIGTERM]
    assert stop.is_set() is False


def test_install_signal_handlers_falls_back_on_windows(monkeypatch) -> None:
    """Если loop не поддерживает сигналы, используется signal.signal."""
    registered: list[int] = []
    monkeypatch.setattr(
        signal, "signal", lambda sig, handler: registered.append(sig)
    )
    loop = FakeLoop(fail=True)
    stop = asyncio.Event()

    install_signal_handlers(loop, stop)  # type: ignore[arg-type]

    assert registered == [signal.SIGINT, signal.SIGTERM]
    assert stop.is_set() is False
