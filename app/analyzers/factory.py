"""Фабрика AI-анализатора."""

from __future__ import annotations

import logging

from app.analyzers.base import Analyzer
from app.analyzers.deepseek import DEFAULT_BASE_URL, DEFAULT_MODEL, DeepSeekAnalyzer
from app.analyzers.null import NullAnalyzer
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
