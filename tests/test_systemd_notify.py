"""Тесты systemd notify без реального $NOTIFY_SOCKET."""

from __future__ import annotations

import asyncio

import pytest

from app import systemd_notify
from app.systemd_notify import SystemdNotifier


class FakeSocket:
    """Подделка unix datagram socket."""

    def __init__(self) -> None:
        self.address = ""
        self.sent: list[bytes] = []

    def __enter__(self) -> "FakeSocket":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def connect(self, address: str) -> None:
        self.address = address

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


def test_noop_without_notify_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без NOTIFY_SOCKET notify возвращает False, run завершается."""
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    notifier = SystemdNotifier()

    assert notifier.notify("READY=1") is False
    asyncio.run(notifier.run())


def test_notify_sends_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """READY=1 уходит на NOTIFY_SOCKET."""
    fake = FakeSocket()
    monkeypatch.setenv("NOTIFY_SOCKET", "/tmp/notify.sock")
    monkeypatch.setattr(
        systemd_notify.socket_module, "socket", lambda *args: fake
    )

    notifier = SystemdNotifier()

    assert notifier.notify("READY=1") is True
    assert fake.address == "/tmp/notify.sock"
    assert fake.sent == [b"READY=1"]


def test_notify_abstract_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Адрес @name превращается в abstract-namespace с ведущим NUL."""
    fake = FakeSocket()
    monkeypatch.setenv("NOTIFY_SOCKET", "@silverwatch")
    monkeypatch.setattr(
        systemd_notify.socket_module, "socket", lambda *args: fake
    )

    notifier = SystemdNotifier()
    assert notifier.notify("WATCHDOG=1") is True
    assert fake.address == "\0silverwatch"


def test_run_sends_ready_then_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    """run отправляет READY и затем WATCHDOG (сон прерываем для теста)."""
    messages: list[str] = []
    monkeypatch.setenv("NOTIFY_SOCKET", "/tmp/notify.sock")
    notifier = SystemdNotifier()
    monkeypatch.setattr(
        notifier, "notify", lambda message: messages.append(message) or True
    )

    async def fake_sleep(seconds: float) -> None:
        if messages == ["READY=1"]:
            return
        raise asyncio.CancelledError

    monkeypatch.setattr(systemd_notify.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(notifier.run())

    assert messages == ["READY=1", "WATCHDOG=1"]
