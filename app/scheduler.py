"""Планировщик: опрос Kufar, дедупликация, bootstrap и уведомления."""

from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

from app.analyzers.base import AiVerdict, Analyzer
from app.analyzers.factory import build_analyzer
from app.analyzers.null import NullAnalyzer
from app.config import Config, QueryConfig
from app.control import DEFAULT_CONTROL_PATH, ControlState, read_control, write_control
from app.kufar import KufarClient, KufarError
from app.models import Ad
from app.notifier import Notifier
from app.scrap import DEFAULT_SCRAP_PATH, ScrapPrices, load_scrap_prices
from app.silver import SilverAnalysis, analyze_ad
from app.sleep import SleepInhibitor
from app.status import DEFAULT_STATUS_PATH, write_status
from app.storage import Storage, utc_now_iso

logger = logging.getLogger(__name__)

BOOTSTRAP_TEXT = "✅ Запущен. «{query}»: {count} объявлений. Слежу за новыми."
DIGEST_TEXT = "Ещё {count} новых по запросу «{query}»: {url}"
KUFAR_PAUSE_SECONDS = 5 * 60
NOTICE_INTERVAL = timedelta(minutes=30)
LAST_KUFAR_NOTICE_KEY = "last_kufar_notice_at"
KUFAR_ERROR_ACTIVE = "kufar_error_active"
KUFAR_ERROR_QUERY = "kufar_error_query"
KUFAR_ERROR_KIND = "kufar_error_kind"
KUFAR_ERROR_STATUS = "kufar_error_status"
KUFAR_RECOVERY_TEXT = "✅ Kufar снова отвечает"
STALE_ALERT_INTERVAL = timedelta(minutes=30)
STALE_CHECK_INTERVAL_SECONDS = 30
STALE_ALERT_PREFIX = "stale_alert:"
ERROR_429_TEXT = "⚠️ Kufar: 429, пауза 5 минут"
LAST_429_NOTICE_KEY = LAST_KUFAR_NOTICE_KEY  # совместимость со старым именем
AI_HINT_RE = re.compile(
    r"серебр|925|875|лом|монет|ложк|вилк|цеп|кольц|серьг|браслет",
    re.IGNORECASE,
)


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
        scrap_prices_path: str | Path | None = None,
        sleep_inhibitor: SleepInhibitor | None = None,
        analyzer: Analyzer | None = None,
    ) -> None:
        self.config = config
        self.kufar = kufar
        self.storage = storage
        self.notifier = notifier
        self.control_path = Path(control_path or DEFAULT_CONTROL_PATH)
        self.status_path = Path(status_path or DEFAULT_STATUS_PATH)
        self.scrap_prices_path = Path(scrap_prices_path or DEFAULT_SCRAP_PATH)
        self.sleep = sleep_inhibitor or SleepInhibitor()
        self.analyzer = analyzer or build_analyzer(config)
        self._http_lock = asyncio.Lock()
        self._manual_scan_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._ensure_control_file()
        state = read_control(self.control_path)
        self.tracking = state.tracking
        self.prevent_sleep = state.prevent_sleep
        self._started_at = datetime.now(timezone.utc)

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

    async def _fetch_detail(self, url: str) -> str:
        """Загрузить detail-страницу под тем же глобальным HTTP lock."""
        async with self._http_lock:
            return await asyncio.to_thread(self.kufar.fetch_detail, url)

    async def _get_body(self, ad: Ad) -> str | None:
        """Взять body из кэша/raw или загрузить detail; при ошибке None."""
        cached = self.storage.get_ad_detail(ad.ad_id)
        if cached is not None:
            return cached

        raw_body = ad.raw.get("body") if isinstance(ad.raw, dict) else None
        if isinstance(raw_body, str) and raw_body.strip():
            self.storage.save_ad_detail(ad.ad_id, raw_body)
            return raw_body

        try:
            body = await self._fetch_detail(ad.url)
        except Exception:
            logger.exception(
                "Kufar detail: не удалось получить описание ad_id=%s", ad.ad_id
            )
            return None
        self.storage.save_ad_detail(ad.ad_id, body)
        return body

    async def run_manual_scan(self) -> dict[str, int]:
        """Один проход по всем запросам вне расписания.

        Не меняет tracking, не запускает bootstrap и не ждёт следующего
        интервала. Параллельный вызов отклоняется.
        """
        if self._manual_scan_lock.locked():
            logger.info("Ручной скан уже выполняется, повторный запуск отклонён")
            return {"new": 0, "sent": 0}

        prices = load_scrap_prices(self.scrap_prices_path)
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
                    result = await self._regular_pass(
                        query, ignore_tracking=True, prices=prices
                    )
                except KufarError as error:
                    logger.exception(
                        "Ручной скан: Kufar не ответил по «%s»", query.q
                    )
                    await self._notify_kufar_error(error, query.q)
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
        except KufarError as error:
            logger.exception("Kufar: запрос «%s» не выполнен", query.q)
            await self._notify_kufar_error(error, query.q)
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
        await self._on_successful_poll(query.q)
        logger.info("Bootstrap «%s»: сохранено %d объявлений", query.q, len(ads))
        return PassResult(new=new_count)

    async def _maybe_ai(
        self,
        ad: Ad,
        query: QueryConfig,
        analysis: SilverAnalysis | None,
        prices: ScrapPrices,
        *,
        allow_api: bool,
    ) -> AiVerdict | None:
        """Вернуть AI-вердикт из кэша или запросить его для нового ad."""
        if isinstance(self.analyzer, NullAnalyzer):
            return None
        cached = self.storage.get_ai_verdict(ad.ad_id)
        if cached is not None:
            return cached
        if not allow_api:
            return None
        if analysis is None:
            return None

        body = await self._get_body(ad)
        if body is None:
            return None
        hint_text = f"{ad.subject}\n{body}"
        if not (
            analysis.is_silver
            or query.notify
            or AI_HINT_RE.search(hint_text)
        ):
            return None

        today = datetime.now(timezone.utc).date().isoformat()
        calls_key = f"llm_calls:{today}"
        try:
            calls = int(self.storage.get_meta(calls_key) or 0)
        except ValueError:
            calls = 0
        if calls >= self.config.llm_max_calls_per_day:
            logger.info("AI: дневной лимит %d исчерпан", self.config.llm_max_calls_per_day)
            return None
        self.storage.set_meta(calls_key, str(calls + 1))

        try:
            verdict = await self.analyzer.analyze(ad, body, analysis, prices)
        except Exception:
            logger.exception("AI: ошибка анализа ad_id=%s", ad.ad_id)
            return None
        if verdict is None:
            logger.info("AI: fallback для ad_id=%s", ad.ad_id)
            return None
        self.storage.save_ai_verdict(ad.ad_id, verdict)
        logger.info(
            "AI: ad_id=%s is_silver=%s verdict=%s confidence=%.2f",
            ad.ad_id,
            verdict.is_silver,
            verdict.verdict,
            verdict.confidence,
        )
        return verdict

    async def _analyze_new_ad(
        self, ad: Ad, prices: ScrapPrices
    ) -> SilverAnalysis | None:
        """Загрузить detail (если нужно) и сохранить разбор нового ad."""
        body = await self._get_body(ad)
        if body is None:
            return None
        try:
            analysis = analyze_ad(
                ad,
                body,
                prices,
                lead_max_price_byn=self.config.limits.lead_max_price_byn,
                lead_negotiable=self.config.limits.lead_negotiable,
            )
        except Exception:
            logger.exception("silver: не удалось разобрать ad_id=%s", ad.ad_id)
            return None
        self.storage.save_analysis(ad.ad_id, analysis)
        return analysis

    async def _regular_pass(
        self,
        query: QueryConfig,
        *,
        ignore_tracking: bool = False,
        prices: ScrapPrices | None = None,
    ) -> PassResult:
        """Обычный проход: сохранить новое и отправить уведомления."""
        ads = await self._fetch(query.q)
        if not ignore_tracking and not self.tracking:
            logger.info("Отслеживание выключено во время запроса «%s»", query.q)
            return PassResult()
        if prices is None:
            prices = load_scrap_prices(self.scrap_prices_path)
        new_count = 0
        sent = 0
        skipped = 0
        limit = self.config.limits.max_alerts_per_cycle

        for ad in ads:
            is_new = self.storage.is_new(ad.ad_id)
            if is_new:
                new_count += 1
            self.storage.save(ad, query.q)

            if self.storage.is_instant_alerted(ad.ad_id):
                continue
            if is_older_than(ad, self.config.limits.catchup_max_age_hours):
                self.storage.mark_instant_alerted(ad.ad_id)
                continue

            analysis: SilverAnalysis | None = None
            if is_new:
                analysis = await self._analyze_new_ad(ad, prices)
            else:
                analysis = self.storage.get_analysis(ad.ad_id)

            ai = await self._maybe_ai(
                ad,
                query,
                analysis,
                prices,
                allow_api=is_new,
            )

            if self.storage.is_instant_alerted(ad.ad_id):
                continue
            if ai is not None and ai.verdict == "skip":
                logger.info("AI: skip ad_id=%s", ad.ad_id)
                continue
            if ai is not None:
                if not ai.is_silver:
                    logger.info(
                        "AI: серебро не подтверждено, но verdict=%s — отправляю "
                        "как лид ad_id=%s",
                        ai.verdict,
                        ad.ad_id,
                    )
                should_send = True
            else:
                should_send = (
                    analysis is not None
                    and (
                        analysis.is_below_scrap
                        or analysis.is_suspicious
                        or analysis.is_lead
                    )
                ) or query.notify
            if not should_send:
                continue
            if sent >= limit:
                self.storage.mark_instant_alerted(ad.ad_id)
                skipped += 1
                continue

            ok = await self.notifier.send_ad(ad, query.q, analysis, ai)
            self.storage.mark_instant_alerted(ad.ad_id)
            self.storage.log_alert(
                ad.ad_id,
                query.q,
                ok=ok,
                error=None if ok else "не удалось отправить",
            )
            sent += 1
            if ai is not None:
                reason = f"AI {ai.verdict}"
            elif analysis is not None and analysis.is_below_scrap:
                reason = "ниже лома"
            elif analysis is not None and analysis.is_suspicious:
                reason = "подозрительно дешево"
            elif analysis is not None and analysis.is_lead:
                reason = "лид"
            else:
                reason = "notify"
            logger.info(
                "Алерт ad_id=%s, запрос «%s», причина=%s", ad.ad_id, query.q, reason
            )

        if skipped:
            text = DIGEST_TEXT.format(
                count=skipped,
                query=html.escape(query.q, quote=False),
                url=search_url(query.q),
            )
            await self.notifier.send_text(text)
            logger.info("Дайджест «%s»: ещё %d новых", query.q, skipped)

        self.storage.set_last_poll(query.q, utc_now_iso())
        await self._on_successful_poll(query.q)
        return PassResult(new=new_count, sent=sent)

    async def _notify_kufar_error(self, error: KufarError, query: str) -> None:
        """Запомнить ошибку Kufar и сообщить о ней не чаще раза в 30 минут."""
        self.storage.set_meta(KUFAR_ERROR_ACTIVE, "1")
        self.storage.set_meta(KUFAR_ERROR_QUERY, query)
        self.storage.set_meta(KUFAR_ERROR_KIND, error.kind)
        if error.status_code is not None:
            self.storage.set_meta(KUFAR_ERROR_STATUS, str(error.status_code))
        else:
            self.storage.delete_meta(KUFAR_ERROR_STATUS)

        now = datetime.now(timezone.utc)
        last = self.storage.get_meta(LAST_KUFAR_NOTICE_KEY)
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
            except ValueError:
                last_dt = None
            if last_dt is not None:
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if now - last_dt < NOTICE_INTERVAL:
                    logger.info(
                        "Kufar: уведомление об ошибке пропущено (лимит 30 минут)"
                    )
                    return

        if error.kind == "rate_limit":
            text = "⚠️ Kufar: 429, пауза 5 минут"
        elif error.kind == "network":
            text = "⚠️ Kufar: нет связи, пауза 5 минут"
        elif error.kind == "http":
            code = error.status_code if error.status_code is not None else "?"
            text = f"⚠️ Kufar: HTTP {code}, пауза 5 минут"
        else:
            text = "⚠️ Kufar: не удалось разобрать ответ"

        await self.notifier.send_text(text)
        self.storage.set_meta(LAST_KUFAR_NOTICE_KEY, now.isoformat())

    async def _on_successful_poll(self, query: str) -> None:
        """Сообщить о восстановлении после ошибки Kufar/stale-опроса."""
        if (
            self.storage.get_meta(KUFAR_ERROR_ACTIVE) == "1"
            and self.storage.get_meta(KUFAR_ERROR_QUERY) == query
        ):
            await self.notifier.send_text(KUFAR_RECOVERY_TEXT)
            for key in (
                KUFAR_ERROR_ACTIVE,
                KUFAR_ERROR_QUERY,
                KUFAR_ERROR_KIND,
                KUFAR_ERROR_STATUS,
            ):
                self.storage.delete_meta(key)

        stale_key = f"{STALE_ALERT_PREFIX}{query}"
        if self.storage.get_meta(stale_key):
            await self.notifier.send_text(
                f"✅ Опрос «{html.escape(query, quote=False)}» восстановлен"
            )
            self.storage.delete_meta(stale_key)

    async def check_stale_polls(self) -> None:
        """Проверить зависшие опросы и предупредить не чаще раза в 30 минут."""
        if not self.tracking:
            return
        now = datetime.now(timezone.utc)
        for query in self.config.queries:
            if not self.storage.is_bootstrapped(query.q):
                continue
            last_poll = self.storage.last_poll(query.q)
            if last_poll:
                try:
                    last_dt = datetime.fromisoformat(last_poll)
                except ValueError:
                    logger.warning(
                        "stale: некорректный last_poll для «%s»", query.q
                    )
                    continue
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
            else:
                last_dt = self._started_at

            age = (now - last_dt).total_seconds()
            threshold = max(
                self.config.limits.stale_poll_sec,
                query.interval * 2 + 60,
            )
            if age <= threshold:
                continue

            stale_key = f"{STALE_ALERT_PREFIX}{query.q}"
            last_alert = self.storage.get_meta(stale_key)
            if last_alert:
                try:
                    alert_dt = datetime.fromisoformat(last_alert)
                except ValueError:
                    alert_dt = None
                if alert_dt is not None:
                    if alert_dt.tzinfo is None:
                        alert_dt = alert_dt.replace(tzinfo=timezone.utc)
                    if now - alert_dt < STALE_ALERT_INTERVAL:
                        continue

            minutes = max(1, int(age // 60))
            await self.notifier.send_text(
                f"⚠️ Нет успешных опросов «{html.escape(query.q, quote=False)}» "
                f"{minutes} минут"
            )
            self.storage.set_meta(stale_key, now.isoformat())
            logger.warning(
                "stale: «%s» без успешного опроса %d мин", query.q, minutes
            )

    async def _stale_watchdog(self) -> None:
        """Раз в 30 секунд проверять stale-опросы."""
        while True:
            try:
                await self.check_stale_polls()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("stale watchdog: ошибка проверки")
            await asyncio.sleep(STALE_CHECK_INTERVAL_SECONDS)

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
        tasks.append(asyncio.create_task(self._stale_watchdog(), name="stale"))
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
