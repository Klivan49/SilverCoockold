"""Клиент API Kufar.

Синхронный клиент: один запрос за раз, без пагинации.
Планировщик будет вызывать fetch() через asyncio.to_thread.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.models import Ad

logger = logging.getLogger(__name__)

API_URL = "https://api.kufar.by/search-api/v2/search/rendered-paginated"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

MAX_RETRIES = 3
HTTP_RETRY_DELAYS = (30, 60, 120)
NETWORK_RETRY_DELAYS = (5, 15, 30)
MAX_RETRY_AFTER = 120


class KufarError(Exception):
    """Ошибка запроса к Kufar после исчерпания ретраев."""


class KufarClient:
    """Клиент поиска Kufar с ретраями и нормализацией объявлений."""

    def __init__(
        self,
        timeout: float = 20.0,
        proxy_url: str | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleep = sleep
        self._owns_client = client is None

        if client is None:
            kwargs: dict[str, Any] = {
                "timeout": timeout,
                "follow_redirects": True,
                "headers": {
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                },
            }
            if proxy_url:
                kwargs["proxy"] = proxy_url
            client = httpx.Client(**kwargs)

        self._client = client

    def fetch(self, query: str) -> list[Ad]:
        """Забрать первую страницу выдачи и вернуть нормализованные объявления."""
        attempt = 0
        started = time.monotonic()

        while True:
            try:
                response = self._client.get(
                    API_URL,
                    params={"query": query, "size": 100},
                )
            except httpx.HTTPError as exc:
                attempt += 1
                if attempt > MAX_RETRIES:
                    raise KufarError(
                        f"Kufar: сеть недоступна после {MAX_RETRIES} повторов "
                        f"(query={query!r}): {exc}"
                    ) from exc
                delay = NETWORK_RETRY_DELAYS[attempt - 1]
                logger.warning(
                    "Kufar: сетевая ошибка (query=%r), повтор %d/%d через %d с: %s",
                    query,
                    attempt,
                    MAX_RETRIES,
                    delay,
                    exc,
                )
                self._sleep(delay)
                continue

            if response.status_code == 200:
                ads = self._parse(response, query)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.info(
                    "Kufar: query=%r, HTTP 200, объявлений=%d, %d мс",
                    query,
                    len(ads),
                    elapsed_ms,
                )
                return ads

            if response.status_code == 429 or 500 <= response.status_code < 600:
                attempt += 1
                if attempt > MAX_RETRIES:
                    raise KufarError(
                        f"Kufar: HTTP {response.status_code} после "
                        f"{MAX_RETRIES} повторов (query={query!r})"
                    )
                delay = HTTP_RETRY_DELAYS[attempt - 1]
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = min(max(int(retry_after), 0), MAX_RETRY_AFTER)
                    except ValueError:
                        pass
                logger.warning(
                    "Kufar: HTTP %d (query=%r), повтор %d/%d через %d с",
                    response.status_code,
                    query,
                    attempt,
                    MAX_RETRIES,
                    delay,
                )
                self._sleep(delay)
                continue

            raise KufarError(
                f"Kufar: HTTP {response.status_code} (query={query!r}), "
                "повтор не поможет"
            )

    def _parse(self, response: httpx.Response, query: str) -> list[Ad]:
        """Разобрать JSON-ответ, пропуская битые объявления."""
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise KufarError(
                f"Kufar: не удалось разобрать JSON (query={query!r})"
            ) from exc

        if not isinstance(payload, dict):
            raise KufarError(f"Kufar: ожидался объект JSON (query={query!r})")

        raw_ads = payload.get("ads")
        if not isinstance(raw_ads, list):
            raise KufarError(f"Kufar: в ответе нет списка ads (query={query!r})")

        ads: list[Ad] = []
        for index, raw in enumerate(raw_ads):
            if not isinstance(raw, dict):
                logger.warning("Kufar: объявление #%d не объект, пропуск", index)
                continue

            ad_id = raw.get("ad_id") or raw.get("list_id")
            url = raw.get("ad_link")
            if not ad_id or not url:
                logger.warning(
                    "Kufar: объявление #%d без ad_id или ad_link, пропуск", index
                )
                continue

            list_time = raw.get("list_time")
            ads.append(
                Ad(
                    ad_id=str(ad_id),
                    subject=str(raw.get("subject") or ""),
                    price_byn=self._parse_price(raw.get("price_byn")),
                    url=str(url),
                    list_time=list_time if isinstance(list_time, str) else None,
                    raw=raw,
                )
            )

        return ads

    @staticmethod
    def _parse_price(value: Any) -> int | None:
        """Привести price_byn к копейкам; 0, пустое и мусор — None."""
        if value is None or value == "":
            return None
        try:
            price = int(value)
        except (TypeError, ValueError):
            logger.warning("Kufar: некорректная цена %r, считаю договорной", value)
            return None
        return price if price > 0 else None

    def close(self) -> None:
        """Закрыть HTTP-клиент, если он создан внутри KufarClient."""
        if self._owns_client:
            self._client.close()
