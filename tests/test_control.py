"""Тесты control.json: defaults, битый JSON, roundtrip и атомарность."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pytest

from app import control
from app.control import ControlState, read_control, write_control


def test_missing_file_returns_defaults(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Нет файла → значения по умолчанию и предупреждение."""
    path = tmp_path / "control.json"

    with caplog.at_level(logging.WARNING, logger="app.control"):
        state = read_control(path)

    assert state == ControlState(tracking=True, prevent_sleep=False)
    assert "не найден" in caplog.text


def test_broken_json_returns_defaults(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Битый JSON не роняет чтение."""
    path = tmp_path / "control.json"
    path.write_text("{это не json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="app.control"):
        state = read_control(path)

    assert state == ControlState()
    assert "не удалось прочитать" in caplog.text


def test_wrong_types_return_defaults(tmp_path: Path) -> None:
    """Строки вместо bool трактуются как ошибка и дают defaults."""
    path = tmp_path / "control.json"
    path.write_text(
        json.dumps({"tracking": "yes", "prevent_sleep": 1}),
        encoding="utf-8",
    )

    assert read_control(path) == ControlState()


def test_roundtrip_and_updated_at(tmp_path: Path) -> None:
    """write_control → read_control сохраняет оба поля и updated_at."""
    path = tmp_path / "control.json"
    write_control(path, ControlState(tracking=False, prevent_sleep=True))

    payload = json.loads(path.read_text(encoding="utf-8"))
    datetime.fromisoformat(payload["updated_at"])
    assert read_control(path) == ControlState(tracking=False, prevent_sleep=True)


def test_atomic_replace_keeps_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """При сбое os.replace старый файл не портится, tmp удаляется."""
    path = tmp_path / "control.json"
    write_control(path, ControlState(tracking=True))
    original = path.read_text(encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(control.os, "replace", fail_replace)

    with pytest.raises(OSError):
        write_control(path, ControlState(tracking=False))

    assert path.read_text(encoding="utf-8") == original
    assert not (tmp_path / "control.json.tmp").exists()
