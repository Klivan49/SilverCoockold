"""Клиент API Kufar.

Заглушка шага 1: сеть не используется, реализация появится на шаге 2.
"""

from app.models import Ad


class KufarError(Exception):
    """Ошибка запроса к Kufar после исчерпания ретраев."""


class KufarClient:
    """Заглушка клиента Kufar: всегда возвращает пустой список."""

    def fetch(self, query: str) -> list[Ad]:
        """Вернуть пустой список, не обращаясь к сети."""
        return []
