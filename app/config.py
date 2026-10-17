"""Загрузка config.yaml и .env, валидация настроек."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.yaml"
DEFAULT_DB_PATH = "./data/silverwatch.db"

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigError(Exception):
    """Ошибка конфигурации с понятным пользователю текстом."""


@dataclass(frozen=True)
class QueryConfig:
    """Один поисковый запрос из config.yaml."""

    q: str
    interval: int
    instant: bool
    notify: bool


@dataclass(frozen=True)
class LimitsConfig:
    """Ограничения, защищающие от лавины уведомлений."""

    max_alerts_per_cycle: int
    catchup_max_age_hours: float
    request_timeout: float


@dataclass(frozen=True)
class Config:
    """Полная конфигурация приложения."""

    queries: list[QueryConfig]
    limits: LimitsConfig
    telegram_token: str | None
    telegram_chat_id: str | None
    db_path: Path
    dry_run: bool
    log_level: str
    proxy_url: str | None


def _load_yaml(path: Path) -> dict[str, Any]:
    """Прочитать YAML-файл и вернуть словарь верхнего уровня."""
    if not path.is_file():
        raise ConfigError(f"не найден файл конфигурации: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"не удалось разобрать {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: ожидается YAML-словарь верхнего уровня")
    return data


def _positive_int(value: Any, name: str) -> int:
    """Проверить, что значение — положительное целое число."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"config.yaml: {name} должен быть положительным целым числом")
    return value


def _positive_number(value: Any, name: str) -> float:
    """Проверить, что значение — положительное число."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"config.yaml: {name} должен быть положительным числом")
    return float(value)


def _parse_queries(raw: Any) -> list[QueryConfig]:
    """Разобрать и проверить раздел queries."""
    if not isinstance(raw, list) or not raw:
        raise ConfigError("config.yaml: раздел queries должен быть непустым списком")
    queries: list[QueryConfig] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ConfigError(f"config.yaml: queries[{index}] должен быть словарём")
        q = item.get("q")
        if not isinstance(q, str) or not q.strip():
            raise ConfigError(f"config.yaml: queries[{index}].q должен быть непустой строкой")
        interval = item.get("interval")
        if not isinstance(interval, int) or isinstance(interval, bool) or interval <= 0:
            raise ConfigError(
                f"config.yaml: queries[{index}].interval должен быть положительным целым числом"
            )
        instant = item.get("instant", False)
        notify = item.get("notify", False)
        if not isinstance(instant, bool) or not isinstance(notify, bool):
            raise ConfigError(
                f"config.yaml: queries[{index}].instant и queries[{index}].notify "
                "должны быть true или false"
            )
        queries.append(
            QueryConfig(q=q.strip(), interval=interval, instant=instant, notify=notify)
        )
    return queries


def _parse_limits(raw: Any) -> LimitsConfig:
    """Разобрать раздел limits, подставив значения по умолчанию."""
    data = raw if isinstance(raw, dict) else {}
    return LimitsConfig(
        max_alerts_per_cycle=_positive_int(
            data.get("max_alerts_per_cycle", 20), "limits.max_alerts_per_cycle"
        ),
        catchup_max_age_hours=_positive_number(
            data.get("catchup_max_age_hours", 12), "limits.catchup_max_age_hours"
        ),
        request_timeout=_positive_number(
            data.get("request_timeout", 20), "limits.request_timeout"
        ),
    )


def _parse_bool(value: str | None, default: bool) -> bool:
    """Разобрать булево значение из env: 1/0, true/false, yes/no, on/off."""
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ConfigError(f"DRY_RUN: ожидается 1 или 0, получено {value!r}")


def load_config(config_path: Path | None = None) -> Config:
    """Загрузить .env и config.yaml, проверить обязательные настройки."""
    load_dotenv(BASE_DIR / ".env")

    path = config_path or CONFIG_PATH
    data = _load_yaml(path)

    queries = _parse_queries(data.get("queries"))
    limits = _parse_limits(data.get("limits"))

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip() or None
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip() or None
    dry_run = _parse_bool(os.getenv("DRY_RUN"), default=True)

    log_level = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    if log_level not in LOG_LEVELS:
        raise ConfigError(
            f"LOG_LEVEL: допустимые значения {', '.join(sorted(LOG_LEVELS))}"
        )

    db_path = Path(os.getenv("DB_PATH") or DEFAULT_DB_PATH).expanduser()
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    proxy_url = (os.getenv("PROXY_URL") or "").strip() or None

    if not dry_run and (token is None or chat_id is None):
        raise ConfigError(
            "при DRY_RUN=0 обязательны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID"
        )

    return Config(
        queries=queries,
        limits=limits,
        telegram_token=token,
        telegram_chat_id=chat_id,
        db_path=db_path,
        dry_run=dry_run,
        log_level=log_level,
        proxy_url=proxy_url,
    )
