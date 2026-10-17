"""Планировщик: опрос Kufar, дедупликация, bootstrap и уведомления."""

from __future__ import annotations

import asyncio
import html
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

from app.config import Config, QueryConfig
from app.control import DEFAULT_CONTROL_PATH, ControlState, read_control, write_control
from app.kufar import KufarClient, KufarError
from app.models import Ad
from app.notifier import Notifier
from app.sleep import SleepInhibitor
from app.status import DEFAULT_STATUS_PATH, write_status
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
        control_path: str | Path | None = None,
        status_path: str | Path | None = None,
        sleep_inhibitor: SleepInhibitor | None = None,
    ) -> None:
        self.config = config
        self.kufar = kufar
        self.storage = storage
        self.notifier = notifier
        self.control_path = Path(control_path or DEFAULT_CONTROL_PATH)
        self.status_path = Path(status_path or DEFAULT_STATUS_PATH)
        self.sleep = sleep_inhibitor or SleepInhibitor()
        self._http_lock = asyncio.Lock()
        self._manual_scan_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._ensure_control_file()
        state = read_control(self.control_path)
        self.tracking = state.tracking
        self.prevent_sleep = state.prevent_sleep

    def _ensure_control_file(self) -> None:
        """Создать control.json с настройками по умолчанию, если файла нет."""
        if self.control_path.exists():
            return
        write_control(self.control_path, ControlState())
        logger.info("Создан %s с настройками по умолчанию", self.control_path)

    @property
    def is_manual_scan_running(self) -> bool:
        """Идёт ли сейчас ручной скан."""
        return self._manual_scan_lock.locked()

    async def _fetch(self, query: str) -> list[Ad]:
        """Сделать HTTP-запрос последовательно с остальными задачами."""
        async with self._http_lock:
            return await asyncio.to_thread(self.kufar.fetch, query)

    async def run_manual_scan(self) -> dict[str, int]:
        """Один проход по всем запросам вне расписания.

        Не меняет tracking, не запускает bootstrap и не ждёт следующего
        интервала. Параллельный вызов отклоняется.
        """
        if self._manual_scan_lock.locked():
            logger.info("Ручной скан уже выполняется, повторный запуск отклонён")
            return {"new": 0, "sent": 0}

        total_new = 0
        total_sent = 0
        async with self._manual_scan_lock:
            for query in self.config.queries:
                if not self.storage.is_bootstrapped(query.q):
                    logger.warning(
                        "Ручной скан: «%s» ещё не bootstrapped, пропуск", query.q
                    )
                    continue
                try:
                    result = await self._regular_pass(query, ignore_tracking=True)
                except KufarError:
                    logger.exception(
                        "Ручной скан: Kufar не ответил по «%s»", query.q
                    )
                    await self._notify_429()
                    continue
                except Exception:
                    logger.exception(
                        "Ручной скан: ошибка прохода по «%s»", query.q
                    )
                    continue
                total_new += result.new
                total_sent += result.sent

            self._write_status()
            logger.info(
                "Ручной скан завершён: новых=%d, отправлено=%d",
                total_new,
                total_sent,
            )
            return {"new": total_new, "sent": total_sent}

    async def run_pass(self, query: QueryConfig) -> PassResult:
        """Выполнить один проход по запросу и вернуть статистику."""
        if not self.tracking:
            logger.info("Отслеживание выключено: проход «%s» пропущен", query.q)
            return PassResult()

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
        self._write_status()
        return result

    async def _bootstrap_pass(self, query: QueryConfig) -> PassResult:
        """Первый проход: сохранить всё, ничего не присылать, кроме сводки."""
        ads = await self._fetch(query.q)
        if not self.tracking:
            logger.info("Отслеживание выключено во время запроса «%s»", query.q)
            return PassResult()
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

    async def _regular_pass(
        self, query: QueryConfig, *, ignore_tracking: bool = False
    ) -> PassResult:
        """Обычный проход: сохранить новое и отправить уведомления."""
        ads = await self._fetch(query.q)
        if not ignore_tracking and not self.tracking:
            logger.info("Отслеживание выключено во время запроса «%s»", query.q)
            return PassResult()
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
        """Запустить циклы запросов, наблюдатель и писателя статуса."""
        if self.prevent_sleep:
            self.sleep.set_prevent_sleep(True)
        tasks = [
            asyncio.create_task(self._query_loop(query), name=f"query:{query.q}")
            for query in self.config.queries
        ]
        tasks.append(asyncio.create_task(self._control_watcher(), name="control"))
        tasks.append(asyncio.create_task(self._status_writer(), name="status"))
        logger.info("Планировщик запущен: задач %d", len(tasks))
        return tasks

    async def stop(self, tasks: list[asyncio.Task[None]]) -> None:
        """Отменить задачи, снять запрет сна и записать running=false."""
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.sleep.close()
        self._write_status(running=False)
        logger.info("Планировщик остановлен")

    async def _control_watcher(self) -> None:
        """Раз в секунду применять control.json без перезапуска демона."""
        while True:
            state = read_control(self.control_path)
            if state.tracking != self.tracking:
                self.tracking = state.tracking
                logger.info(
                    "Отслеживание %s", "включено" if state.tracking else "выключено"
                )
                self._wake.set()
            if state.prevent_sleep != self.prevent_sleep:
                self.prevent_sleep = state.prevent_sleep
                logger.info(
                    "Режим сна %s",
                    "запрещён" if state.prevent_sleep else "разрешён",
                )
                self.sleep.set_prevent_sleep(state.prevent_sleep)
            await asyncio.sleep(1.0)

    async def _status_writer(self) -> None:
        """Обновлять status.json раз в 5 секунд."""
        while True:
            self._write_status()
            await asyncio.sleep(5.0)

    def _write_status(self, running: bool = True) -> None:
        """Записать status.json, не роняя демон при ошибке БД/диска."""
        try:
            stats = self.storage.stats()
            write_status(
                self.status_path,
                running=running,
                tracking=self.tracking,
                prevent_sleep=self.prevent_sleep,
                ads_total=int(stats["ads_total"]),
                last_poll=dict(stats["last_poll"]),
            )
        except Exception:
            logger.exception("Не удалось записать status.json")

    async def _query_loop(self, query: QueryConfig) -> None:
        """Бесконечный цикл опроса одного запроса с джиттером."""
        while True:
            if not self.tracking:
                await self._wake.wait()
                self._wake.clear()
                continue

            result = await self.run_pass(query)
            if result.paused:
                delay = float(KUFAR_PAUSE_SECONDS)
            else:
                delay = interval_with_jitter(query.interval)
            logger.info("«%s»: следующий опрос через %.1f с", query.q, delay)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            else:
                self._wake.clear()
