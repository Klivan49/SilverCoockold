"""Заглушка анализатора: не выполняет никакой обработки."""

from app.models import Ad


class NullAnalyzer:
    """Анализатор, который всегда возвращает None."""

    async def analyze(self, ad: Ad) -> None:
        """Вернуть None: анализ в MVP не используется."""
        return None
