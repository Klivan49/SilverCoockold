"""Внешний healthcheck: GET HEALTHCHECK_URL раз в 60 секунд."""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.scheduler import Scheduler

logger = logging.getLogger(__name__)

HEALTHCHECK_INTERVAL_SECONDS = 60
HEALTHCHECK_TIMEOUT_SECONDS = 10


class Healthcheck:
    """Пингер внешнего сервиса мониторинга; ошибки только в лог."""

    def __init__(
        self,
        url: str | None,
        scheduler: Scheduler,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = url
        self.scheduler = scheduler
        self._client = client

    async def ping_once(self) -> bool:
        """Сделать один GET, если есть URL, tracking и успешный опрос."""
        if not self.url or not self.scheduler.tracking:
            return False
        polls = self.scheduler.storage.stats().get("last_poll") or {}
        if not polls:
            return False

        own_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=HEALTHCHECK_TIMEOUT_SECONDS
        )
        try:
            response = await client.get(self.url)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("healthcheck: ошибка запроса")
            return False
        finally:
            if own_client:
                await client.aclose()

        if 200 <= response.status_code < 300:
            return True
        logger.warning("healthcheck: HTTP %d", response.status_code)
        return False

    async def run(self) -> None:
        """Пинги раз в 60 секунд; без URL — no-op."""
        if not self.url:
            logger.info("healthcheck: HEALTHCHECK_URL не задан, выключен")
            return
        while True:
            try:
                await self.ping_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("healthcheck: ошибка цикла")
            await asyncio.sleep(HEALTHCHECK_INTERVAL_SECONDS)
