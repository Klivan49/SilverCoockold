"""Фабрика AI-анализатора."""

from __future__ import annotations

import logging

from app.analyzers.base import Analyzer
from app.analyzers.deepseek import DEFAULT_BASE_URL, DEFAULT_MODEL, DeepSeekAnalyzer
from app.analyzers.null import NullAnalyzer
from app.analyzers.vision import (
    DEFAULT_CLAUDE_BASE_URL,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_FALLBACK_MIN_CONFIDENCE,
    DEFAULT_SESSION_ID,
    DEFAULT_VISION_BASE_URL,
    DEFAULT_VISION_MODEL,
    DeepSeekVisionAnalyzer,
    FallbackVisionAnalyzer,
    NullVisionAnalyzer,
    VisionAnalyzer,
    VisionAnalyzerProtocol,
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


def _make_vision(
    provider: str,
    api_key: str | None,
    *,
    model: str,
    base_url: str,
    timeout: float,
    max_images: int,
    session_id: str,
) -> VisionAnalyzerProtocol | None:
    """Собрать один vision-клиент или None, если он не нужен."""
    if provider in {"", "none"}:
        return None
    if provider not in {"deepseek", "anthropic"}:
        logger.warning(
            "vision: неизвестный провайдер %r, vision выключен", provider
        )
        return None
    if not api_key:
        logger.warning(
            "vision: %s включён, но ключ не задан — vision выключен", provider
        )
        return None

    kwargs = {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "timeout": timeout,
        "max_images": max_images,
        "session_id": session_id,
    }
    if provider == "deepseek":
        return DeepSeekVisionAnalyzer(**kwargs)
    return VisionAnalyzer(**kwargs)


def build_vision_analyzer(
    config: Config,
) -> VisionAnalyzerProtocol | NullVisionAnalyzer:
    """Собрать vision: DeepSeek/Claude, с fallback или без; none/без ключа — Null."""
    session_id = getattr(config, "llm_session_id", DEFAULT_SESSION_ID)
    timeout = getattr(config, "vision_timeout", 60.0)
    max_images = getattr(config, "vision_max_images", 3)

    primary = _make_vision(
        getattr(config, "vision_provider", "none"),
        getattr(config, "vision_api_key", None),
        model=getattr(config, "vision_model", DEFAULT_VISION_MODEL),
        base_url=getattr(
            config, "vision_base_url", DEFAULT_VISION_BASE_URL
        ),
        timeout=timeout,
        max_images=max_images,
        session_id=session_id,
    )
    if primary is None:
        return NullVisionAnalyzer()

    fallback_provider = getattr(
        config, "vision_fallback_provider", "none"
    )
    fallback = _make_vision(
        fallback_provider,
        getattr(config, "vision_fallback_api_key", None),
        model=getattr(
            config, "vision_fallback_model", DEFAULT_CLAUDE_MODEL
        ),
        base_url=getattr(
            config, "vision_fallback_base_url", DEFAULT_CLAUDE_BASE_URL
        ),
        timeout=timeout,
        max_images=max_images,
        session_id=session_id,
    )
    if fallback is None:
        return primary

    if fallback_provider == getattr(config, "vision_provider", "none"):
        logger.info(
            "vision: fallback совпадает с основным провайдером %r", fallback_provider
        )
    return FallbackVisionAnalyzer(
        primary,
        fallback,
        min_confidence=getattr(
            config,
            "vision_min_confidence_fallback",
            DEFAULT_FALLBACK_MIN_CONFIDENCE,
        ),
    )
