"""Тесты запрета сна: no-op без systemd-inhibit и идемпотентность."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app import sleep
from app.sleep import SleepInhibitor


class FakeProcess:
    """Подделка Popen для systemd-inhibit."""

    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self.waited: list[float | None] = []

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self.waited.append(timeout)
        return 0

    def kill(self) -> None:
        self.killed = True


def test_no_systemd_inhibit_is_noop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Без systemd-inhibit включение не падает и предупреждает один раз."""
    monkeypatch.setattr(sleep.shutil, "which", lambda name: None)
    inhibitor = SleepInhibitor()

    with caplog.at_level(logging.WARNING, logger="app.sleep"):
        inhibitor.set_prevent_sleep(True)
        inhibitor.set_prevent_sleep(True)

    assert caplog.text.count("systemd-inhibit не найден") == 1
    assert inhibitor.enabled is True

    inhibitor.set_prevent_sleep(False)
    assert inhibitor.enabled is False


def test_systemd_inhibit_started_once_and_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Повторное включение не запускает второй процесс; выключение — terminate."""
    processes: list[FakeProcess] = []
    commands: list[list[str]] = []

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess()
        processes.append(process)
        commands.append(command)
        return process

    monkeypatch.setattr(
        sleep.shutil, "which", lambda name: "/usr/bin/systemd-inhibit"
    )
    monkeypatch.setattr(sleep.subprocess, "Popen", fake_popen)

    inhibitor = SleepInhibitor()
    inhibitor.set_prevent_sleep(True)
    inhibitor.set_prevent_sleep(True)

    assert len(processes) == 1
    assert commands[0][0] == "/usr/bin/systemd-inhibit"
    assert "--what=sleep" in commands[0]
    assert commands[0][-2:] == ["sleep", "infinity"]
    assert processes[0].terminated is False

    inhibitor.set_prevent_sleep(False)

    assert processes[0].terminated is True
    assert processes[0].killed is False

    inhibitor.set_prevent_sleep(False)
    inhibitor.close()
    assert len(processes) == 1
