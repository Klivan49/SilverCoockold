"""Фабрика AI-анализатора."""

from __future__ import annotations

import logging

from app.analyzers.base import Analyzer
from app.analyzers.deepseek import DEFAULT_BASE_URL, DEFAULT_MODEL, DeepSeekAnalyzer
from app.analyzers.null import NullAnalyzer
from app.analyzers.vision import (
    DEFAULT_VISION_BASE_URL,
    DEFAULT_VISION_MODEL,
    NullVisionAnalyzer,
    VisionAnalyzer,
)
from app.config import Config

logger = logging.getLogger(__name__)


def build_analyzer(config: Config) -> Analyzer:
    """Собрать анализатор по LLM_PROVIDER; без ключа — NullAnalyzer."""
    provider = config.llm_provider
    if provider == "none":
        return NullAnalyzer()
    if provider not in {"deepseek", "openai"}:
        logger.warning("AI: неизвестный провайдер %r, AI выключен", provider)
        return NullAnalyzer()
    if not config.llm_api_key:
        logger.warning("AI: %s включён, но ключ не задан — AI выключен", provider)
        return NullAnalyzer()
    return DeepSeekAnalyzer(
        base_url=config.llm_base_url or DEFAULT_BASE_URL,
        api_key=config.llm_api_key,
        model=config.llm_model or DEFAULT_MODEL,
        timeout=config.llm_timeout,
        session_id=getattr(
            config, "llm_session_id", "ses_silverwatch_ai"
        ),
    )


def build_vision_analyzer(
    config: Config,
) -> VisionAnalyzer | NullVisionAnalyzer:
    """Собрать vision-анализатор; none/без ключа — Null."""
    provider = getattr(config, "vision_provider", "none")
    if provider == "none":
        return NullVisionAnalyzer()
    if provider != "anthropic":
        logger.warning(
            "vision: неизвестный провайдер %r, vision выключен", provider
        )
        return NullVisionAnalyzer()
    if not getattr(config, "vision_api_key", None):
        logger.warning("vision: anthropic включён, но ключ не задан — vision выключен")
        return NullVisionAnalyzer()
    return VisionAnalyzer(
        base_url=getattr(
            config, "vision_base_url", DEFAULT_VISION_BASE_URL
        ),
        api_key=config.vision_api_key or "",
        model=getattr(config, "vision_model", DEFAULT_VISION_MODEL),
        timeout=getattr(config, "vision_timeout", 60.0),
        max_images=getattr(config, "vision_max_images", 3),
        session_id=getattr(
            config, "llm_session_id", "ses_silverwatch_ai"
        ),
    )
