"""Загрузка config.yaml и .env, валидация настроек."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
    force_scan_cooldown_sec: int = 300
    stale_poll_sec: int = 180
    lead_max_price_byn: float = 100.0
    lead_negotiable: bool = True
    require_ai_for_leads: bool = True
    lead_digest_interval_min: int = 30
    lead_digest_max_items: int = 10
    strong_lead_ratio: float = 0.6


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
    telegram_chat_ids: list[str] = field(default_factory=list)
    telegram_admin_ids: list[str] = field(default_factory=list)
    healthcheck_url: str | None = None
    llm_provider: str = "none"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_session_id: str = "ses_silverwatch_ai"
    llm_max_calls_per_day: int = 200
    llm_timeout: float = 20.0
    ai_max_unprofitable_prob: float = 0.4
    ai_min_confidence_digest: float = 0.6
    vision_provider: str = "none"
    vision_model: str = "deepseek-v4.1-flash"
    vision_base_url: str = "https://opencode.ai/inference/go/openai/v1"
    vision_api_key: str | None = None
    vision_max_calls_per_day: int = 100
    vision_max_images: int = 3
    vision_timeout: float = 60.0
    vision_fallback_provider: str = "anthropic"
    vision_fallback_model: str = "claude-haiku-5-5"
    vision_fallback_base_url: str = (
        "https://opencode.ai/inference/go/anthropic/v1"
    )
    vision_fallback_api_key: str | None = None
    vision_min_confidence_fallback: float = 0.5


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


def _non_negative_number(value: Any, name: str) -> float:
    """Проверить, что значение — неотрицательное число."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ConfigError(
            f"config.yaml: {name} должен быть неотрицательным числом"
        )
    return float(value)


def _fraction(value: Any, name: str) -> float:
    """Проверить, что значение — доля в диапазоне (0, 1]."""
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not (0 < value <= 1)
    ):
        raise ConfigError(
            f"config.yaml: {name} должен быть числом в диапазоне 0..1"
        )
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
    lead_negotiable = data.get("lead_negotiable", True)
    if not isinstance(lead_negotiable, bool):
        raise ConfigError(
            "config.yaml: limits.lead_negotiable должен быть true или false"
        )
    require_ai_for_leads = data.get("require_ai_for_leads", True)
    if not isinstance(require_ai_for_leads, bool):
        raise ConfigError(
            "config.yaml: limits.require_ai_for_leads должен быть true или false"
        )
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
        force_scan_cooldown_sec=_positive_int(
            data.get("force_scan_cooldown_sec", 300),
            "limits.force_scan_cooldown_sec",
        ),
        stale_poll_sec=_positive_int(
            data.get("stale_poll_sec", 180),
            "limits.stale_poll_sec",
        ),
        lead_max_price_byn=_non_negative_number(
            data.get("lead_max_price_byn", 100),
            "limits.lead_max_price_byn",
        ),
        lead_negotiable=lead_negotiable,
        require_ai_for_leads=require_ai_for_leads,
        lead_digest_interval_min=_positive_int(
            data.get("lead_digest_interval_min", 30),
            "limits.lead_digest_interval_min",
        ),
        lead_digest_max_items=_positive_int(
            data.get("lead_digest_max_items", 10),
            "limits.lead_digest_max_items",
        ),
        strong_lead_ratio=_fraction(
            data.get("strong_lead_ratio", 0.6),
            "limits.strong_lead_ratio",
        ),
    )


def _env_positive_int(value: str | None, name: str, default: int) -> int:
    """Разобрать положительное целое из env."""
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ConfigError(f"{name}: ожидается целое число") from exc
    if parsed <= 0:
        raise ConfigError(f"{name}: должно быть > 0")
    return parsed


def _env_positive_number(
    value: str | None, name: str, default: float
) -> float:
    """Разобрать положительное число из env."""
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value.strip())
    except ValueError as exc:
        raise ConfigError(f"{name}: ожидается число") from exc
    if parsed <= 0:
        raise ConfigError(f"{name}: должно быть > 0")
    return parsed


def _env_confidence(value: str | None, name: str, default: float) -> float:
    """Разобрать порог уверенности в диапазоне 0..1 из env."""
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value.strip())
    except ValueError as exc:
        raise ConfigError(f"{name}: ожидается число") from exc
    if not (0.0 <= parsed <= 1.0):
        raise ConfigError(f"{name}: должно быть в диапазоне 0..1")
    return parsed


def _parse_id_list(value: str | None, name: str) -> list[str]:
    """Разобрать список ID через запятую: пробелы и пустые элементы допустимы."""
    if value is None or not value.strip():
        return []
    result: list[str] = []
    for part in value.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            numeric = int(item)
        except ValueError as exc:
            raise ConfigError(
                f"{name}: ожидается числовой ID, получено {item!r}"
            ) from exc
        if numeric == 0:
            raise ConfigError(f"{name}: ID не может быть нулём")
        normalized = str(numeric)
        if normalized not in result:
            result.append(normalized)
    return result


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
    legacy_chat_ids = _parse_id_list(
        os.getenv("TELEGRAM_CHAT_ID"), "TELEGRAM_CHAT_ID"
    )
    chat_ids = _parse_id_list(
        os.getenv("TELEGRAM_CHAT_IDS"), "TELEGRAM_CHAT_IDS"
    )
    if not chat_ids:
        chat_ids = legacy_chat_ids
    admin_ids = _parse_id_list(
        os.getenv("TELEGRAM_ADMIN_IDS"), "TELEGRAM_ADMIN_IDS"
    )
    if not admin_ids:
        admin_ids = list(chat_ids)
    chat_id = legacy_chat_ids[0] if legacy_chat_ids else (
        chat_ids[0] if chat_ids else None
    )
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

    healthcheck_url = (os.getenv("HEALTHCHECK_URL") or "").strip() or None
    if healthcheck_url is not None:
        parsed = urlparse(healthcheck_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigError(
                "HEALTHCHECK_URL должен быть полным http(s)-адресом"
            )

    provider = (os.getenv("LLM_PROVIDER") or "none").strip().lower()
    if provider in {"", "null"}:
        provider = "none"
    if provider not in {"none", "deepseek", "openai"}:
        raise ConfigError(
            "LLM_PROVIDER: допустимые значения none, deepseek, openai"
        )
    llm_base_url = (
        (os.getenv("LLM_BASE_URL") or "").strip()
        or "https://api.deepseek.com/v1"
    )
    parsed_base = urlparse(llm_base_url)
    if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
        raise ConfigError("LLM_BASE_URL должен быть полным http(s)-адресом")
    llm_api_key = (os.getenv("LLM_API_KEY") or "").strip() or None
    llm_model = (os.getenv("LLM_MODEL") or "").strip() or "deepseek-chat"
    llm_session_id = (
        (os.getenv("LLM_SESSION_ID") or "").strip() or "ses_silverwatch_ai"
    )
    llm_max_calls = _env_positive_int(
        os.getenv("LLM_MAX_CALLS_PER_DAY"), "LLM_MAX_CALLS_PER_DAY", 200
    )
    llm_timeout = _env_positive_number(
        os.getenv("LLM_TIMEOUT"), "LLM_TIMEOUT", 20.0
    )
    ai_max_unprofitable_prob = _env_confidence(
        os.getenv("AI_MAX_UNPROFITABLE_PROB"),
        "AI_MAX_UNPROFITABLE_PROB",
        0.4,
    )
    ai_min_confidence_digest = _env_confidence(
        os.getenv("AI_MIN_CONFIDENCE_DIGEST"),
        "AI_MIN_CONFIDENCE_DIGEST",
        0.6,
    )

    vision_provider = (os.getenv("VISION_PROVIDER") or "none").strip().lower()
    if vision_provider in {"", "null"}:
        vision_provider = "none"
    if vision_provider not in {"none", "deepseek", "anthropic"}:
        raise ConfigError(
            "VISION_PROVIDER: допустимые значения none, deepseek, anthropic"
        )
    vision_base_url = (
        (os.getenv("VISION_BASE_URL") or "").strip()
        or "https://opencode.ai/inference/go/openai/v1"
    )
    parsed_vision = urlparse(vision_base_url)
    if parsed_vision.scheme not in {"http", "https"} or not parsed_vision.netloc:
        raise ConfigError("VISION_BASE_URL должен быть полным http(s)-адресом")
    vision_api_key = (
        (os.getenv("VISION_API_KEY") or "").strip() or llm_api_key
    )
    vision_model = (
        (os.getenv("VISION_MODEL") or "").strip() or "deepseek-v4.1-flash"
    )
    vision_max_calls = _env_positive_int(
        os.getenv("VISION_MAX_CALLS_PER_DAY"),
        "VISION_MAX_CALLS_PER_DAY",
        100,
    )
    vision_max_images = _env_positive_int(
        os.getenv("VISION_MAX_IMAGES"), "VISION_MAX_IMAGES", 3
    )
    vision_timeout = _env_positive_number(
        os.getenv("VISION_TIMEOUT"), "VISION_TIMEOUT", 60.0
    )

    vision_fallback_provider = (
        (os.getenv("VISION_FALLBACK_PROVIDER") or "anthropic").strip().lower()
    )
    if vision_fallback_provider in {"", "null"}:
        vision_fallback_provider = "none"
    if vision_fallback_provider not in {"none", "deepseek", "anthropic"}:
        raise ConfigError(
            "VISION_FALLBACK_PROVIDER: допустимые значения none, deepseek, "
            "anthropic"
        )
    vision_fallback_base_url = (
        (os.getenv("VISION_FALLBACK_BASE_URL") or "").strip()
        or "https://opencode.ai/inference/go/anthropic/v1"
    )
    parsed_vision_fallback = urlparse(vision_fallback_base_url)
    if (
        parsed_vision_fallback.scheme not in {"http", "https"}
        or not parsed_vision_fallback.netloc
    ):
        raise ConfigError(
            "VISION_FALLBACK_BASE_URL должен быть полным http(s)-адресом"
        )
    vision_fallback_api_key = (
        (os.getenv("VISION_FALLBACK_API_KEY") or "").strip() or llm_api_key
    )
    vision_fallback_model = (
        (os.getenv("VISION_FALLBACK_MODEL") or "").strip()
        or "claude-haiku-5-5"
    )
    vision_min_confidence_fallback = _env_confidence(
        os.getenv("VISION_MIN_CONFIDENCE_FALLBACK"),
        "VISION_MIN_CONFIDENCE_FALLBACK",
        0.5,
    )

    if not dry_run and token is None:
        raise ConfigError("при DRY_RUN=0 обязателен TELEGRAM_BOT_TOKEN")
    if not dry_run and not chat_ids:
        raise ConfigError(
            "при DRY_RUN=0 нужен хотя бы один получатель: "
            "TELEGRAM_CHAT_IDS или TELEGRAM_CHAT_ID"
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
        telegram_chat_ids=chat_ids,
        telegram_admin_ids=admin_ids,
        healthcheck_url=healthcheck_url,
        llm_provider=provider,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        llm_session_id=llm_session_id,
        llm_max_calls_per_day=llm_max_calls,
        llm_timeout=llm_timeout,
        ai_max_unprofitable_prob=ai_max_unprofitable_prob,
        ai_min_confidence_digest=ai_min_confidence_digest,
        vision_provider=vision_provider,
        vision_model=vision_model,
        vision_base_url=vision_base_url,
        vision_api_key=vision_api_key,
        vision_max_calls_per_day=vision_max_calls,
        vision_max_images=vision_max_images,
        vision_timeout=vision_timeout,
        vision_fallback_provider=vision_fallback_provider,
        vision_fallback_model=vision_fallback_model,
        vision_fallback_base_url=vision_fallback_base_url,
        vision_fallback_api_key=vision_fallback_api_key,
        vision_min_confidence_fallback=vision_min_confidence_fallback,
    )
