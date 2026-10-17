"""Модели данных проекта."""

from dataclasses import dataclass


@dataclass
class Ad:
    """Объявление Kufar в нормализованном виде."""

    ad_id: str
    subject: str
    price_byn: int | None  # в копейках, как отдаёт Kufar
    url: str
    list_time: str | None  # ISO-8601
    raw: dict
