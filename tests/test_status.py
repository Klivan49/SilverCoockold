"""Тесты status.json: запись, чтение и определение живости демона."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.status import is_alive, read_status, write_status


def _write(path: Path, *, running: bool = True, tracking: bool = True) -> None:
    """Записать тестовый статус."""
    write_status(
        path,
        running=running,
        tracking=tracking,
        prevent_sleep=False,
        ads_total=328,
        last_poll={"серебро лом": "2026-10-17T10:00:00+00:00"},
    )


def test_missing_status_is_none(tmp_path: Path) -> None:
    """Нет status.json → None, без исключения."""
    assert read_status(tmp_path / "status.json") is None


def test_roundtrip_and_alive(tmp_path: Path) -> None:
    """Свежий status.json читается и считается живым."""
    path = tmp_path / "status.json"
    _write(path)

    status = read_status(path)

    assert status is not None
    assert status["running"] is True
    assert status["ads_total"] == 328
    assert status["last_poll"] == {"серебро лом": "2026-10-17T10:00:00+00:00"}
    datetime.fromisoformat(status["updated_at"])
    assert is_alive(status) is True


def test_stale_status_is_not_alive(tmp_path: Path) -> None:
    """Старый updated_at → связи нет."""
    path = tmp_path / "status.json"
    _write(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=31)
    ).isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert is_alive(read_status(path)) is False


def test_stopped_daemon_is_not_alive(tmp_path: Path) -> None:
    """running=false → демон не считается живым даже при свежем времени."""
    path = tmp_path / "status.json"
    _write(path, running=False)

    assert is_alive(read_status(path)) is False
