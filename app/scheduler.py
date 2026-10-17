"""Планировщик: опрос Kufar, дедупликация, bootstrap и уведомления."""

from __future__ import annotations

import asyncio
import html
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from app.config import Config, QueryConfig
from app.kufar import KufarClient, KufarError
from app.models import Ad
from app.notifier import Notifier
from app.storage import Storage, utc_now_iso

logger = logging.getLogger(__name__)

BOOTSTRAP_TEXT = "✅ Запущен. «{query}»: {count} объявлений. Слежу за новыми."
DIGEST_TEXT = "Ещё {count} новых по запросу «{query}»: {url}"
ERROR_429_TEXT = "⚠️ Kufar 429, пауза 5 минут"
KUFAR_PAUSE_SECONDS = 5 * 60
NOTICE_INTERVAL = timedelta(minutes=30)
LAST_429_NOTICE_KEY = "last_429_notice_at"


@dataclass
class PassResult:
    """Итог одного прохода по одному запросу."""

    new: int = 0
    sent: int = 0
    paused: bool = False


def search_url(query: str) -> str:
    """Ссылка на поиск Kufar для дайджеста."""
    return f"https://www.kufar.by/l?query={quote_plus(query)}"


def interval_with_jitter(interval: int) -> float:
    """Интервал опроса с джиттером ±20%."""
    return interval * random.uniform(0.8, 1.2)


def is_older_than(ad: Ad, hours: float) -> bool:
    """Проверить, что объявление старше указанного числа часов."""
    if not ad.list_time:
        return False
    try:
        listed = datetime.fromisoformat(ad.list_time.replace("Z", "+00:00"))
    except ValueError:
        return False
    if listed.tzinfo is None:
        listed = listed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - listed > timedelta(hours=hours)


class Scheduler:
    """Асинхронный планировщик с одним HTTP-запросом к Kufar за раз."""

    def __init__(
        self,
        config: Config,
        kufar: KufarClient,
        storage: Storage,
        notifier: Notifier,
    ) -> None:
        self.config = config
        self.kufar = kufar
        self.storage = storage
        self.notifier = notifier
        self._http_lock = asyncio.Lock()

    async def _fetch(self, query: str) -> list[Ad]:
        """Сделать HTTP-запрос последовательно с остальными задачами."""
        async with self._http_lock:
            return await asyncio.to_thread(self.kufar.fetch, query)

    async def run_pass(self, query: QueryConfig) -> PassResult:
        """Выполнить один проход по запросу и вернуть статистику."""
        started = time.monotonic()
        try:
            if self.storage.is_bootstrapped(query.q):
                result = await self._regular_pass(query)
            else:
                result = await self._bootstrap_pass(query)
        except KufarError:
            logger.exception("Kufar: запрос «%s» не выполнен", query.q)
            await self._notify_429()
            logger.warning("Kufar: пауза 5 минут после ошибки по «%s»", query.q)
            result = PassResult(paused=True)
        except Exception:
            logger.exception("Планировщик: ошибка прохода по «%s»", query.q)
            result = PassResult()

        duration_ms = (time.monotonic() - started) * 1000
        logger.info(
            "Проход «%s»: новых=%d, отправлено=%d, %d мс",
            query.q,
            result.new,
            result.sent,
            duration_ms,
        )
        return result

    async def _bootstrap_pass(self, query: QueryConfig) -> PassResult:
        """Первый проход: сохранить всё, ничего не присылать, кроме сводки."""
        ads = await self._fetch(query.q)
        new_count = 0
        for ad in ads:
            if self.storage.is_new(ad.ad_id):
                new_count += 1
            self.storage.save(ad, query.q)
            self.storage.mark_instant_alerted(ad.ad_id)
        self.storage.set_bootstrapped(query.q)
        self.storage.set_last_poll(query.q, utc_now_iso())

        text = BOOTSTRAP_TEXT.format(
            query=html.escape(query.q, quote=False), count=len(ads)
        )
        await self.notifier.send_text(text)
        logger.info("Bootstrap «%s»: сохранено %d объявлений", query.q, len(ads))
        return PassResult(new=new_count)

    async def _regular_pass(self, query: QueryConfig) -> PassResult:
        """Обычный проход: сохранить новое и отправить уведомления."""
        ads = await self._fetch(query.q)
        new_count = 0
        sent = 0
        skipped = 0
        limit = self.config.limits.max_alerts_per_cycle

        for ad in ads:
            if self.storage.is_new(ad.ad_id):
                new_count += 1
            self.storage.save(ad, query.q)

            if self.storage.is_instant_alerted(ad.ad_id):
                continue
            if is_older_than(ad, self.config.limits.catchup_max_age_hours):
                self.storage.mark_instant_alerted(ad.ad_id)
                continue
            if not query.notify:
                continue
            if sent >= limit:
                self.storage.mark_instant_alerted(ad.ad_id)
                skipped += 1
                continue

            ok = await self.notifier.send_ad(ad, query.q)
            self.storage.mark_instant_alerted(ad.ad_id)
            self.storage.log_alert(
                ad.ad_id,
                query.q,
                ok=ok,
                error=None if ok else "не удалось отправить",
            )
            sent += 1

        if skipped:
            text = DIGEST_TEXT.format(
                count=skipped,
                query=html.escape(query.q, quote=False),
                url=search_url(query.q),
            )
            await self.notifier.send_text(text)
            logger.info("Дайджест «%s»: ещё %d новых", query.q, skipped)

        self.storage.set_last_poll(query.q, utc_now_iso())
        return PassResult(new=new_count, sent=sent)

    async def _notify_429(self) -> None:
        """Сообщить админу о 429, но не чаще одного раза в 30 минут."""
        now = datetime.now(timezone.utc)
        last = self.storage.get_meta(LAST_429_NOTICE_KEY)
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
            except ValueError:
                last_dt = None
            if last_dt is not None:
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if now - last_dt < NOTICE_INTERVAL:
                    logger.info("Kufar 429: уведомление админу пропущено (лимит 30 минут)")
                    return

        await self.notifier.send_text(ERROR_429_TEXT)
        self.storage.set_meta(LAST_429_NOTICE_KEY, now.isoformat())

    def start(self) -> list[asyncio.Task[None]]:
        """Запустить по задаче на каждый запрос из конфига."""
        tasks = [
            asyncio.create_task(self._query_loop(query), name=f"query:{query.q}")
            for query in self.config.queries
        ]
        logger.info("Планировщик запущен: задач %d", len(tasks))
        return tasks

    async def stop(self, tasks: list[asyncio.Task[None]]) -> None:
        """Отменить задачи и дождаться их завершения."""
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Планировщик остановлен")

    async def _query_loop(self, query: QueryConfig) -> None:
        """Бесконечный цикл опроса одного запроса с джиттером."""
        while True:
            result = await self.run_pass(query)
            if result.paused:
                delay = float(KUFAR_PAUSE_SECONDS)
            else:
                delay = interval_with_jitter(query.interval)
            logger.info("«%s»: следующий опрос через %.1f с", query.q, delay)
            await asyncio.sleep(delay)
