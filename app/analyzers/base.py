"""Интерфейс анализатора объявлений."""

from typing import Protocol

from app.models import Ad


class Analyzer(Protocol):
    """Протокол анализатора: сейчас NullAnalyzer, позже — AI."""

    async def analyze(self, ad: Ad) -> None:
        """Проанализировать объявление и вернуть результат или None."""
        ...
