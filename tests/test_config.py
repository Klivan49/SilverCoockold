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
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "")
    monkeypatch.setenv("TELEGRAM_ADMIN_IDS", "")
    monkeypatch.setenv("HEALTHCHECK_URL", "")
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_MAX_CALLS_PER_DAY", "")
    monkeypatch.setenv("LLM_TIMEOUT", "")
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


def test_stale_poll_sec_parsed_and_default(tmp_path: Path) -> None:
    """stale_poll_sec читается и по умолчанию равен 180."""
    default_config = load_config(write_config(tmp_path, base_limits()))
    assert default_config.limits.stale_poll_sec == 180

    limits = base_limits() | {"stale_poll_sec": 60}
    config = load_config(write_config(tmp_path, limits))
    assert config.limits.stale_poll_sec == 60


def test_stale_poll_sec_validation(tmp_path: Path) -> None:
    """stale_poll_sec обязан быть целым > 0."""
    limits = base_limits() | {"stale_poll_sec": 0}
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, limits))


def test_healthcheck_url_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HEALTHCHECK_URL сохраняется; пустое значение допустимо."""
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc.example/ping")
    config = load_config(write_config(tmp_path, base_limits()))
    assert config.healthcheck_url == "https://hc.example/ping"

    monkeypatch.setenv("HEALTHCHECK_URL", "")
    config = load_config(write_config(tmp_path, base_limits()))
    assert config.healthcheck_url is None


def test_healthcheck_url_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Некорректный URL отклоняется."""
    monkeypatch.setenv("HEALTHCHECK_URL", "ftp://example/ping")
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, base_limits()))


def test_lead_limits_defaults_and_parsing(tmp_path: Path) -> None:
    """Лид-настройки читаются, по умолчанию 100 BYN и true."""
    default_config = load_config(write_config(tmp_path, base_limits()))
    assert default_config.limits.lead_max_price_byn == 100.0
    assert default_config.limits.lead_negotiable is True

    limits = base_limits() | {
        "lead_max_price_byn": 0,
        "lead_negotiable": False,
    }
    config = load_config(write_config(tmp_path, limits))
    assert config.limits.lead_max_price_byn == 0.0
    assert config.limits.lead_negotiable is False


@pytest.mark.parametrize(
    "limits",
    [
        {"lead_max_price_byn": -1},
        {"lead_max_price_byn": "100"},
        {"lead_negotiable": "yes"},
    ],
)
def test_lead_limits_validation(tmp_path: Path, limits: dict) -> None:
    """Невалидные лид-настройки отклоняются."""
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, base_limits() | limits))


def test_llm_defaults(tmp_path: Path) -> None:
    """Без LLM_* AI выключен и параметры безопасны."""
    config = load_config(write_config(tmp_path, base_limits()))

    assert config.llm_provider == "none"
    assert config.llm_api_key is None
    assert config.llm_base_url == "https://api.deepseek.com/v1"
    assert config.llm_model == "deepseek-chat"
    assert config.llm_max_calls_per_day == 200
    assert config.llm_timeout == 20.0


def test_llm_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Провайдер, ключ, модель и лимиты читаются из env."""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "custom-model")
    monkeypatch.setenv("LLM_MAX_CALLS_PER_DAY", "5")
    monkeypatch.setenv("LLM_TIMEOUT", "7")

    config = load_config(write_config(tmp_path, base_limits()))

    assert config.llm_provider == "deepseek"
    assert config.llm_base_url == "https://api.example/v1"
    assert config.llm_api_key == "secret"
    assert config.llm_model == "custom-model"
    assert config.llm_max_calls_per_day == 5
    assert config.llm_timeout == 7.0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LLM_PROVIDER", "unknown"),
        ("LLM_BASE_URL", "ftp://example/v1"),
        ("LLM_MAX_CALLS_PER_DAY", "0"),
        ("LLM_TIMEOUT", "-1"),
    ],
)
def test_llm_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    """Невалидные LLM-настройки отклоняются."""
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, base_limits()))


def test_chat_ids_parsing_and_admin_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Списки парсятся с пробелами, пустыми элементами и fallback админов."""
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", " 111, 222 ,,333 ")
    monkeypatch.setenv("TELEGRAM_ADMIN_IDS", "")

    config = load_config(write_config(tmp_path, base_limits()))

    assert config.telegram_chat_ids == ["111", "222", "333"]
    assert config.telegram_admin_ids == ["111", "222", "333"]
    assert config.telegram_chat_id == "111"


def test_admin_ids_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Отдельный список админов сохраняется."""
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "111,222")
    monkeypatch.setenv("TELEGRAM_ADMIN_IDS", " 222 ")

    config = load_config(write_config(tmp_path, base_limits()))

    assert config.telegram_chat_ids == ["111", "222"]
    assert config.telegram_admin_ids == ["222"]


def test_legacy_chat_id_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Если новых списков нет, используется старый TELEGRAM_CHAT_ID."""
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")

    config = load_config(write_config(tmp_path, base_limits()))

    assert config.telegram_chat_ids == ["987654321"]
    assert config.telegram_admin_ids == ["987654321"]
    assert config.telegram_chat_id == "987654321"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TELEGRAM_CHAT_IDS", "111,abc"),
        ("TELEGRAM_ADMIN_IDS", "0"),
    ],
)
def test_invalid_id_lists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    """Нечисловые и нулевые ID отклоняются."""
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "111")
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, base_limits()))
