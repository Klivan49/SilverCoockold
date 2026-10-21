"""Интерфейс AI-анализатора объявлений."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import Ad
from app.scrap import ScrapPrices
from app.silver import SilverAnalysis


@dataclass
class AiVerdict:
    """Вердикт AI по объявлению."""

    is_silver: bool
    category: str
    verdict: str
    reason: str
    question: str
    weight_g: float | None
    purity: int | None
    confidence: float
    model: str = ""


class Analyzer(Protocol):
    """Протокол AI-анализатора."""

    async def analyze(
        self,
        ad: Ad,
        body: str,
        regex_analysis: SilverAnalysis,
        prices: ScrapPrices,
    ) -> AiVerdict | None:
        """Проанализировать объявление и вернуть вердикт или None."""
        ...
