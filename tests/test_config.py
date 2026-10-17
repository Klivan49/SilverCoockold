"""Тесты конфигурации, включая кулдаун принудительного скана."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config import ConfigError, load_config


def write_config(tmp_path: Path, limits: dict) -> Path:
    """Записать временный config.yaml."""
    path = tmp_path / "config.yaml"
    data = {
        "queries": [
            {"q": "серебро лом", "interval": 15, "instant": True, "notify": True}
        ],
        "limits": limits,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def base_limits() -> dict:
    """Лимиты без нового поля."""
    return {
        "max_alerts_per_cycle": 20,
        "catchup_max_age_hours": 12,
        "request_timeout": 20,
    }


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Не дать реальному .env повлиять на валидацию в тестах."""
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.setenv("DB_PATH", "./data/test.db")


def test_force_scan_cooldown_parsed(tmp_path: Path) -> None:
    """Кулдаун читается из limits."""
    limits = base_limits() | {"force_scan_cooldown_sec": 120}

    config = load_config(write_config(tmp_path, limits))

    assert config.limits.force_scan_cooldown_sec == 120


def test_force_scan_cooldown_default(tmp_path: Path) -> None:
    """Без поля кулдаун по умолчанию 300 секунд."""
    config = load_config(write_config(tmp_path, base_limits()))

    assert config.limits.force_scan_cooldown_sec == 300


@pytest.mark.parametrize("value", [0, -1, "300", 1.5, True])
def test_force_scan_cooldown_validation(tmp_path: Path, value: object) -> None:
    """Кулдаун обязан быть целым > 0."""
    limits = base_limits() | {"force_scan_cooldown_sec": value}

    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, limits))
