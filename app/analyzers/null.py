"""Заглушка анализатора: не выполняет никакой обработки."""

from app.models import Ad
from app.scrap import ScrapPrices
from app.silver import SilverAnalysis


class NullAnalyzer:
    """Анализатор, который всегда возвращает None."""

    async def analyze(
        self,
        ad: Ad,
        body: str,
        regex_analysis: SilverAnalysis,
        prices: ScrapPrices,
    ) -> None:
        """Вернуть None: AI выключен."""
        return None
