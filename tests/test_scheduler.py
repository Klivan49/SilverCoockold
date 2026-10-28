"""Тесты планировщика: bootstrap, дедупликация, лимит, catchup и 429."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import scheduler as scheduler_module
from app.analyzers.base import AiVerdict
from app.analyzers.vision import VisionVerdict
from app.config import Config, LimitsConfig, QueryConfig
from app.control import ControlState, write_control
from app.kufar import AdDetail, KufarError
from app.models import Ad
from app.scrap import default_scrap_prices
from app.scheduler import (
    ERROR_429_TEXT,
    KUFAR_ERROR_ACTIVE,
    KUFAR_ERROR_KIND,
    KUFAR_ERROR_QUERY,
    KUFAR_RECOVERY_TEXT,
    LAST_KUFAR_NOTICE_KEY,
    LEAD_DIGEST_LAST_KEY,
    STALE_ALERT_PREFIX,
    Scheduler,
    interval_with_jitter,
    is_older_than,
    search_url,
)
from app.status import read_status
from app.silver import SilverAnalysis
from app.storage import Storage


class FakeKufar:
    """Подделка KufarClient: отдаёт заранее заданные ответы без сети."""

    def __init__(
        self,
        responses: dict[str, list[Ad] | Exception],
        delay: float = 0.0,
        details: dict[str, str] | None = None,
        detail_error: Exception | None = None,
    ) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.detail_calls: list[str] = []
        self.details = details or {}
        self.detail_error = detail_error
        self.detail_images: dict[str, list[str]] = {}
        self.delay = delay

    def fetch(self, query: str) -> list[Ad]:
        self.calls.append(query)
        if self.delay:
            time.sleep(self.delay)
        response = self.responses.get(query, [])
        if isinstance(response, Exception):
            raise response
        return list(response)

    def fetch_detail(self, url: str) -> str:
        return self.fetch_detail_data(url).body

    def fetch_detail_data(self, url: str) -> AdDetail:
        self.detail_calls.append(url)
        if self.detail_error is not None:
            raise self.detail_error
        return AdDetail(
            body=self.details.get(url, ""),
            gallery=list(self.detail_images.get(url, [])),
        )


class FakeNotifier:
    """Подделка Notifier: записывает вызовы, ничего не отправляет."""

    def __init__(self) -> None:
        self.ad_calls: list[tuple[Ad, str]] = []
        self.analyses: list[object] = []
        self.ai_calls: list[object] = []
        self.vision_calls: list[object] = []
        self.texts: list[str] = []
        self.text_result = True
        self.closed = False

    async def send_ad(
        self,
        ad: Ad,
        query: str,
        analysis: object | None = None,
        ai: object | None = None,
        vision: object | None = None,
    ) -> bool:
        self.ad_calls.append((ad, query))
        self.analyses.append(analysis)
        self.ai_calls.append(ai)
        self.vision_calls.append(vision)
        return True

    async def send_text(self, text: str) -> bool:
        self.texts.append(text)
        return self.text_result

    async def close(self) -> None:
        self.closed = True


class FakeSleep:
    """Подделка SleepInhibitor: запоминает вызовы."""

    def __init__(self) -> None:
        self.values: list[bool] = []

    def set_prevent_sleep(self, enabled: bool) -> None:
        self.values.append(enabled)

    def close(self) -> None:
        self.values.append(False)


class FakeAnalyzer:
    """Подделка AI-анализатора."""

    def __init__(self, verdict: AiVerdict | None) -> None:
        self.verdict = verdict
        self.calls = 0

    async def analyze(self, ad, body, regex_analysis, prices):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self.verdict


class FakeVisionAnalyzer:
    """Подделка vision-анализатора."""

    def __init__(self, verdict: VisionVerdict | None) -> None:
        self.verdict = verdict
        self.calls = 0
        self.images: list[object] = []

    async def analyze(self, ad, body, images):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.images.append(images)
        return self.verdict


def vision_verdict(
    *,
    is_silver: bool = True,
    item_type: str = "украшение",
    weight_g: float | None = None,
    purity: int | None = None,
    hallmark: str = "не видно",
    stones_or_costume: bool = False,
    confidence: float = 0.9,
    notes: str = "Фото проверено",
    question: str = "Подтвердите вес",
) -> VisionVerdict:
    """Собрать vision-вердикт для тестов."""
    return VisionVerdict(
        is_silver=is_silver,
        item_type=item_type,
        weight_g=weight_g,
        purity=purity,
        hallmark=hallmark,
        scale_visible=weight_g is not None,
        stones_or_costume=stones_or_costume,
        confidence=confidence,
        notes=notes,
        question=question,
        model="fake-vision",
    )


def ai_verdict(
    is_silver: bool = True, verdict: str = "look"
) -> AiVerdict:
    """Собрать AI-вердикт для тестов."""
    return AiVerdict(
        is_silver=is_silver,
        category="украшение",
        verdict=verdict,
        reason="Стоит проверить",
        question="Есть ли клеймо?",
        weight_g=None,
        purity=None,
        confidence=0.9,
        model="fake",
    )


def make_query(
    q: str = "серебро лом",
    interval: int = 15,
    instant: bool = True,
    notify: bool = True,
) -> QueryConfig:
    """Собрать настройки одного запроса."""
    return QueryConfig(q=q, interval=interval, instant=instant, notify=notify)


def make_config(
    queries: list[QueryConfig],
    max_alerts: int = 20,
    catchup_hours: float = 12,
    llm_max_calls: int = 200,
    require_ai_for_leads: bool = True,
    digest_interval: int = 120,
    digest_max_items: int = 20,
    vision_max_calls: int = 100,
) -> Config:
    """Собрать конфиг для тестов без чтения файлов и env."""
    return Config(
        queries=queries,
        limits=LimitsConfig(
            max_alerts_per_cycle=max_alerts,
            catchup_max_age_hours=catchup_hours,
            request_timeout=20,
            require_ai_for_leads=require_ai_for_leads,
            lead_digest_interval_min=digest_interval,
            lead_digest_max_items=digest_max_items,
        ),
        telegram_token=None,
        telegram_chat_id=None,
        db_path=Path("unused.db"),
        dry_run=True,
        log_level="INFO",
        proxy_url=None,
        llm_provider="none",
        llm_max_calls_per_day=llm_max_calls,
        vision_provider="none",
        vision_max_calls_per_day=vision_max_calls,
    )


def make_ad(
    ad_id: str = "1",
    list_time: str | None = None,
    price_byn: int | None = 2499,
    subject: str | None = None,
) -> Ad:
    """Собрать тестовое объявление."""
    return Ad(
        ad_id=ad_id,
        subject=subject or f"объявление {ad_id}",
        price_byn=price_byn,
        url=f"https://www.kufar.by/item/{ad_id}",
        list_time=list_time,
        raw={"ad_id": ad_id},
    )


def make_scheduler(
    config: Config,
    storage: Storage,
    kufar: FakeKufar,
    notifier: FakeNotifier,
    analyzer: object | None = None,
    vision_analyzer: object | None = None,
) -> Scheduler:
    """Собрать планировщик на подделках с control/status рядом с тестовой БД."""
    base = storage.db_path.parent
    return Scheduler(  # type: ignore[arg-type]
        config,
        kufar,
        storage,
        notifier,
        control_path=base / "control.json",
        status_path=base / "status.json",
        scrap_prices_path=base / "scrap_prices.json",
        sleep_inhibitor=FakeSleep(),
        analyzer=analyzer,
        vision_analyzer=vision_analyzer,
    )


@pytest.mark.parametrize("notify", [True, False])
def test_bootstrap_sends_only_summary(tmp_path: Path, notify: bool) -> None:
    """Bootstrap сохраняет объявления, шлёт сводку и не шлёт алерты."""
    query = make_query(notify=notify)
    storage = Storage(tmp_path / "test.db")
    kufar = FakeKufar({query.q: [make_ad("1"), make_ad("2")]})
    notifier = FakeNotifier()
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    result = asyncio.run(scheduler.run_pass(query))

    assert result.new == 2
    assert result.sent == 0
    assert notifier.ad_calls == []
    assert notifier.texts == [
        "✅ Запущен. «серебро лом»: 2 объявлений. Слежу за новыми."
    ]
    assert storage.is_bootstrapped(query.q) is True
    assert storage.stats()["ads_total"] == 2
    assert storage.is_instant_alerted("1") is True
    assert storage.is_instant_alerted("2") is True


def test_new_ad_sent_once_and_not_duplicated(tmp_path: Path) -> None:
    """Новое объявление отправляется один раз, повторный проход молчит."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    kufar = FakeKufar({query.q: [make_ad("1")]})
    notifier = FakeNotifier()
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    first = asyncio.run(scheduler.run_pass(query))
    second = asyncio.run(scheduler.run_pass(query))

    assert first.new == 1
    assert first.sent == 1
    assert second.sent == 0
    assert len(notifier.ad_calls) == 1
    assert notifier.ad_calls[0][1] == query.q
    assert storage.is_instant_alerted("1") is True

    connection = sqlite3.connect(tmp_path / "test.db")
    try:
        alerts = connection.execute("SELECT ok FROM alerts").fetchall()
    finally:
        connection.close()
    assert alerts == [(1,)]


def test_ordinary_ad_is_alerted_by_priority_query(tmp_path: Path) -> None:
    """Объявление из обычного запроса уведомляется при попадании в приоритетный."""
    ordinary = make_query("серебро", notify=False)
    priority = make_query("серебро лом", notify=True)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(ordinary.q)
    storage.set_bootstrapped(priority.q)
    ad = make_ad("1")
    kufar = FakeKufar({ordinary.q: [ad], priority.q: [ad]})
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([ordinary, priority]), storage, kufar, notifier
    )

    asyncio.run(scheduler.run_pass(ordinary))
    assert notifier.ad_calls == []
    assert storage.is_instant_alerted("1") is False

    asyncio.run(scheduler.run_pass(priority))

    assert len(notifier.ad_calls) == 1
    assert notifier.ad_calls[0][1] == priority.q


def test_limit_and_digest(tmp_path: Path) -> None:
    """Сверх лимита объявления не шлются, уходит один дайджест."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ads = [make_ad("1"), make_ad("2"), make_ad("3")]
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query], max_alerts=2),
        storage,
        FakeKufar({query.q: ads}),
        notifier,
    )

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 2
    assert len(notifier.ad_calls) == 2
    assert notifier.texts == [
        f"Ещё 1 новых по запросу «серебро лом»: {search_url('серебро лом')}"
    ]
    assert all(storage.is_instant_alerted(ad.ad_id) for ad in ads)


def test_old_ad_is_not_alerted(tmp_path: Path) -> None:
    """Объявление старше catchup_max_age_hours только сохраняется."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    old_time = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    old_ad = make_ad("old", list_time=old_time)
    fresh_ad = make_ad("fresh", list_time=datetime.now(timezone.utc).isoformat())
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query], catchup_hours=12),
        storage,
        FakeKufar({query.q: [old_ad, fresh_ad]}),
        notifier,
    )

    result = asyncio.run(scheduler.run_pass(query))

    assert [ad.ad_id for ad, _ in notifier.ad_calls] == ["fresh"]
    assert result.sent == 1
    assert storage.is_instant_alerted("old") is True
    assert storage.is_instant_alerted("fresh") is True


def test_kufar_error_does_not_kill_cycle(tmp_path: Path) -> None:
    """KufarError: уведомление не чаще раза в 30 минут, следующий проход работает."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    kufar = FakeKufar(
        {query.q: KufarError("HTTP 429", kind="rate_limit", status_code=429)}
    )
    notifier = FakeNotifier()
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    first = asyncio.run(scheduler.run_pass(query))
    second = asyncio.run(scheduler.run_pass(query))

    assert first.paused is True
    assert second.paused is True
    assert notifier.texts.count(ERROR_429_TEXT) == 1
    assert storage.get_meta(LAST_KUFAR_NOTICE_KEY) is not None
    assert storage.get_meta(KUFAR_ERROR_ACTIVE) == "1"
    assert storage.get_meta(KUFAR_ERROR_QUERY) == query.q
    assert storage.get_meta(KUFAR_ERROR_KIND) == "rate_limit"

    kufar.responses[query.q] = [make_ad("1")]
    third = asyncio.run(scheduler.run_pass(query))

    assert third.paused is False
    assert third.sent == 1
    assert len(notifier.ad_calls) == 1
    assert notifier.texts[-1] == KUFAR_RECOVERY_TEXT
    assert storage.get_meta(KUFAR_ERROR_ACTIVE) is None

    fourth = asyncio.run(scheduler.run_pass(query))
    assert fourth.paused is False
    assert notifier.texts.count(KUFAR_RECOVERY_TEXT) == 1


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            KufarError("429", kind="rate_limit", status_code=429),
            "⚠️ Kufar: 429, пауза 5 минут",
        ),
        (
            KufarError("network", kind="network"),
            "⚠️ Kufar: нет связи, пауза 5 минут",
        ),
        (
            KufarError("http", kind="http", status_code=503),
            "⚠️ Kufar: HTTP 503, пауза 5 минут",
        ),
        (
            KufarError("parse", kind="parse"),
            "⚠️ Kufar: не удалось разобрать ответ",
        ),
    ],
)
def test_kufar_error_notice_texts(
    tmp_path: Path, error: KufarError, expected: str
) -> None:
    """Текст алерта зависит от kind ошибки."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]), storage, FakeKufar({query.q: error}), notifier
    )

    asyncio.run(scheduler.run_pass(query))

    assert notifier.texts[-1] == expected


def _set_old_poll(storage: Storage, query: str, seconds: int) -> None:
    """Записать last_poll в прошлом."""
    ts = (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).isoformat()
    storage.set_last_poll(query, ts)


def test_stale_poll_alert_and_throttle(tmp_path: Path) -> None:
    """Старый last_poll даёт алерт, повтор в пределах 30 минут — нет."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    _set_old_poll(storage, query.q, 300)
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]), storage, FakeKufar({}), notifier
    )

    asyncio.run(scheduler.check_stale_polls())
    asyncio.run(scheduler.check_stale_polls())

    assert len(notifier.texts) == 1
    assert "Нет успешных опросов «серебро лом» 5 минут" in notifier.texts[0]
    assert storage.get_meta(f"{STALE_ALERT_PREFIX}{query.q}") is not None


def test_stale_fresh_and_disabled_do_not_alert(tmp_path: Path) -> None:
    """Свежий опрос и tracking=false не дают stale-алертов."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    storage.set_last_poll(query.q, datetime.now(timezone.utc).isoformat())
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]), storage, FakeKufar({}), notifier
    )

    asyncio.run(scheduler.check_stale_polls())
    assert notifier.texts == []

    _set_old_poll(storage, query.q, 300)
    scheduler.tracking = False
    asyncio.run(scheduler.check_stale_polls())
    assert notifier.texts == []


def test_stale_recovery(tmp_path: Path) -> None:
    """Успешный опрос после stale-алерта сбрасывает флаг и шлёт recovery."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    _set_old_poll(storage, query.q, 300)
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]), storage, FakeKufar({query.q: []}), notifier
    )

    asyncio.run(scheduler.check_stale_polls())
    asyncio.run(scheduler.run_pass(query))

    assert notifier.texts[-1] == "✅ Опрос «серебро лом» восстановлен"
    assert storage.get_meta(f"{STALE_ALERT_PREFIX}{query.q}") is None


def test_stale_skips_unbootstrapped(tmp_path: Path) -> None:
    """Во время bootstrap stale-алертов нет."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]), storage, FakeKufar({}), notifier
    )

    asyncio.run(scheduler.check_stale_polls())

    assert notifier.texts == []


def test_failed_bootstrap_is_retried(tmp_path: Path) -> None:
    """При ошибке bootstrap флаг не ставится и сводка не отправляется."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]),
        storage,
        FakeKufar({query.q: KufarError("boom")}),
        notifier,
    )

    result = asyncio.run(scheduler.run_pass(query))

    assert result.paused is True
    assert storage.is_bootstrapped(query.q) is False
    assert all("✅" not in text for text in notifier.texts)


SCRAP_BODY = "925 проба, вес 10 г, клеймо"


def test_below_scrap_sent_without_notify(tmp_path: Path) -> None:
    """Ниже лома отправляется даже при notify=false."""
    query = make_query(notify=False)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=1000)
    kufar = FakeKufar({query.q: [ad]}, details={ad.url: SCRAP_BODY})
    notifier = FakeNotifier()
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 1
    assert len(notifier.ad_calls) == 1
    analysis = notifier.analyses[0]
    assert isinstance(analysis, SilverAnalysis)
    assert analysis.is_below_scrap is True
    assert kufar.detail_calls == [ad.url]
    saved = storage.get_analysis(ad.ad_id)
    assert saved is not None and saved.is_below_scrap is True


def test_lead_without_notify_is_queued(tmp_path: Path) -> None:
    """Лид без AI при notify=false уходит в очередь, а не instant."""
    query = make_query(notify=False)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad(
        "lead",
        price_byn=3000,
        subject="Браслет серебряный 16 размер",
    )
    kufar = FakeKufar({query.q: [ad]})
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query], require_ai_for_leads=False),
        storage,
        kufar,
        notifier,
    )

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 0
    assert notifier.ad_calls == []
    assert storage.count_pending() == 1
    assert storage.is_instant_alerted(ad.ad_id) is True
    pending = storage.take_pending(10)
    assert pending[0]["reason"] == "лид"


def test_lead_without_ai_is_skipped_by_default(tmp_path: Path) -> None:
    """Без AI обычные лиды не спамят: только priority/below/suspicious."""
    query = make_query(notify=False)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad(
        "lead-no-ai",
        price_byn=3000,
        subject="Браслет серебряный 16 размер",
    )
    kufar = FakeKufar({query.q: [ad]})
    notifier = FakeNotifier()
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 0
    assert notifier.ad_calls == []
    assert storage.is_instant_alerted(ad.ad_id) is False


def test_above_scrap_not_sent_without_notify(tmp_path: Path) -> None:
    """Выше лома при notify=false не отправляется, но разбор сохраняется."""
    query = make_query(notify=False)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=10000)
    kufar = FakeKufar({query.q: [ad]}, details={ad.url: SCRAP_BODY})
    notifier = FakeNotifier()
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 0
    assert notifier.ad_calls == []
    saved = storage.get_analysis(ad.ad_id)
    assert saved is not None and saved.is_below_scrap is False


def test_priority_sends_analysis_once(tmp_path: Path) -> None:
    """Приоритетный запрос отправляет разбор один раз."""
    query = make_query(notify=True)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=10000)
    kufar = FakeKufar({query.q: [ad]}, details={ad.url: SCRAP_BODY})
    notifier = FakeNotifier()
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    asyncio.run(scheduler.run_pass(query))
    asyncio.run(scheduler.run_pass(query))

    assert len(notifier.ad_calls) == 1
    assert isinstance(notifier.analyses[0], SilverAnalysis)
    assert len(kufar.detail_calls) == 1


def test_raw_body_skips_detail_fetch(tmp_path: Path) -> None:
    """Если в Ad.raw уже есть body, detail не запрашивается."""
    query = make_query(notify=False)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=1000)
    ad.raw["body"] = SCRAP_BODY
    kufar = FakeKufar({query.q: [ad]})
    notifier = FakeNotifier()
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    asyncio.run(scheduler.run_pass(query))

    assert kufar.detail_calls == []
    assert len(notifier.ad_calls) == 1
    assert storage.get_ad_detail(ad.ad_id) == SCRAP_BODY


def test_bootstrap_does_not_fetch_detail(tmp_path: Path) -> None:
    """Bootstrap не тянет detail-страницы."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    ad = make_ad("1", price_byn=1000)
    kufar = FakeKufar({query.q: [ad]}, details={ad.url: SCRAP_BODY})
    notifier = FakeNotifier()
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    asyncio.run(scheduler.run_pass(query))

    assert kufar.detail_calls == []
    assert notifier.ad_calls == []
    assert storage.get_ad_detail(ad.ad_id) is None


def test_catchup_does_not_fetch_detail(tmp_path: Path) -> None:
    """Старое объявление не анализируется и не тянет detail."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    old_time = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    ad = make_ad("old", list_time=old_time, price_byn=1000)
    kufar = FakeKufar({query.q: [ad]}, details={ad.url: SCRAP_BODY})
    notifier = FakeNotifier()
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    asyncio.run(scheduler.run_pass(query))

    assert kufar.detail_calls == []
    assert notifier.ad_calls == []
    assert storage.get_analysis(ad.ad_id) is None


def test_detail_error_sends_regular_alert_without_analysis(tmp_path: Path) -> None:
    """Ошибка detail: обычный алерт уходит без анализа и без scrap-алерта."""
    query = make_query(notify=True)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=1000)
    kufar = FakeKufar(
        {query.q: [ad]},
        details={ad.url: SCRAP_BODY},
        detail_error=KufarError("detail failed"),
    )
    notifier = FakeNotifier()
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 1
    assert len(notifier.ad_calls) == 1
    assert notifier.analyses == [None]


def test_manual_scan_uses_scrap_logic(tmp_path: Path) -> None:
    """Ручной скан тоже отправляет лот ниже лома при notify=false."""
    query = make_query(notify=False)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=1000)
    kufar = FakeKufar({query.q: [ad]}, details={ad.url: SCRAP_BODY})
    notifier = FakeNotifier()
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    result = asyncio.run(scheduler.run_manual_scan())

    assert result == {"new": 1, "sent": 1}
    assert len(notifier.ad_calls) == 1
    assert isinstance(notifier.analyses[0], SilverAnalysis)
    assert kufar.detail_calls == [ad.url]


def test_ai_not_silver_skips_even_notify(tmp_path: Path) -> None:
    """AI is_silver=false блокирует отправку даже при notify=true."""
    query = make_query(notify=True)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=10000, subject="серебро")
    analyzer = FakeAnalyzer(ai_verdict(is_silver=False, verdict="skip"))
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]),
        storage,
        FakeKufar({query.q: [ad]}),
        notifier,
        analyzer=analyzer,
    )

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 0
    assert notifier.ad_calls == []
    assert analyzer.calls == 1


def test_ai_look_sends_with_ai(tmp_path: Path) -> None:
    """AI look отправляет объявление даже при notify=false."""
    query = make_query(notify=False)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=100000, subject="серебро")
    verdict = ai_verdict(verdict="look")
    analyzer = FakeAnalyzer(verdict)
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]),
        storage,
        FakeKufar({query.q: [ad]}),
        notifier,
        analyzer=analyzer,
    )

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 1
    assert len(notifier.ad_calls) == 1
    assert notifier.ai_calls[0] is verdict
    assert storage.get_ai_verdict(ad.ad_id) is not None


def test_ai_none_falls_back_to_regex(tmp_path: Path) -> None:
    """AI None — работает текущая логика notify."""
    query = make_query(notify=True)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=10000, subject="серебро")
    analyzer = FakeAnalyzer(None)
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]),
        storage,
        FakeKufar({query.q: [ad]}),
        notifier,
        analyzer=analyzer,
    )

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 1
    assert analyzer.calls == 1
    assert notifier.ai_calls == [None]


def test_ask_from_ordinary_is_queued_not_sent(tmp_path: Path) -> None:
    """AI ask из обычного запроса уходит в pending без instant."""
    query = make_query(notify=False)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=100000, subject="серебро")
    analyzer = FakeAnalyzer(ai_verdict(verdict="ask"))
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]),
        storage,
        FakeKufar({query.q: [ad]}),
        notifier,
        analyzer=analyzer,
    )

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 0
    assert notifier.ad_calls == []
    assert storage.count_pending() == 1
    assert storage.is_instant_alerted(ad.ad_id) is True
    assert storage.take_pending(10)[0]["reason"] == "ask"


def test_ask_from_priority_with_silver_is_instant(tmp_path: Path) -> None:
    """AI ask из priority с is_silver=true отправляется сразу."""
    query = make_query(notify=True)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=100000, subject="серебро")
    analyzer = FakeAnalyzer(ai_verdict(is_silver=True, verdict="ask"))
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]),
        storage,
        FakeKufar({query.q: [ad]}),
        notifier,
        analyzer=analyzer,
    )

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 1
    assert len(notifier.ad_calls) == 1
    assert storage.count_pending() == 0


def test_ask_from_priority_without_silver_is_queued(tmp_path: Path) -> None:
    """AI ask из priority без подтверждения серебра идёт в дайджест."""
    query = make_query(notify=True)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=100000, subject="серебро")
    analyzer = FakeAnalyzer(ai_verdict(is_silver=False, verdict="ask"))
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]),
        storage,
        FakeKufar({query.q: [ad]}),
        notifier,
        analyzer=analyzer,
    )

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 0
    assert storage.count_pending() == 1


def _queue_digest_item(storage: Storage, ad: Ad, reason: str = "ask") -> None:
    """Подготовить объявление в pending."""
    storage.save(ad, "серебро")
    storage.queue_alert(ad.ad_id, "серебро", reason)


def _set_digest_due(storage: Storage) -> None:
    """Сделать интервал дайджеста истёкшим."""
    old = (datetime.now(timezone.utc) - timedelta(minutes=121)).isoformat()
    storage.set_meta(LEAD_DIGEST_LAST_KEY, old)


def test_digest_sends_pending_and_marks(tmp_path: Path) -> None:
    """Два pending → одно сообщение, оба помечены sent."""
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped("серебро")
    _queue_digest_item(storage, make_ad("1", subject="Браслет серебряный"))
    _queue_digest_item(storage, make_ad("2", subject="Цепочка серебро"))
    _set_digest_due(storage)
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([make_query()]), storage, FakeKufar({}), notifier
    )

    assert asyncio.run(scheduler.send_lead_digest_if_due()) is True
    assert len(notifier.texts) == 1
    assert "Дайджест лидов (2)" in notifier.texts[0]
    assert "Браслет серебряный" in notifier.texts[0]
    assert storage.count_pending() == 0

    assert asyncio.run(scheduler.send_lead_digest_if_due()) is False
    assert len(notifier.texts) == 1


def test_digest_max_items_keeps_remainder(tmp_path: Path) -> None:
    """max_items ограничивает дайджест, остаток остаётся."""
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped("серебро")
    _queue_digest_item(storage, make_ad("1", subject="Первый"))
    _queue_digest_item(storage, make_ad("2", subject="Второй"))
    _set_digest_due(storage)
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([make_query()], digest_max_items=1),
        storage,
        FakeKufar({}),
        notifier,
    )

    assert asyncio.run(scheduler.send_lead_digest_if_due()) is True
    assert storage.count_pending() == 1

    _set_digest_due(storage)
    assert asyncio.run(scheduler.send_lead_digest_if_due()) is True
    assert storage.count_pending() == 0
    assert len(notifier.texts) == 2


def test_digest_send_error_does_not_mark(tmp_path: Path) -> None:
    """Ошибка send_text оставляет pending для повтора."""
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped("серебро")
    _queue_digest_item(storage, make_ad("1", subject="Серебро"))
    _set_digest_due(storage)
    notifier = FakeNotifier()
    notifier.text_result = False
    scheduler = make_scheduler(
        make_config([make_query()]), storage, FakeKufar({}), notifier
    )

    assert asyncio.run(scheduler.send_lead_digest_if_due()) is False
    assert storage.count_pending() == 1
    assert storage.get_meta(LEAD_DIGEST_LAST_KEY) is not None  # old due marker

    notifier.text_result = True
    _set_digest_due(storage)
    assert asyncio.run(scheduler.send_lead_digest_if_due()) is True
    assert storage.count_pending() == 0


def test_empty_digest_sends_nothing(tmp_path: Path) -> None:
    """Нет pending — нет сообщения."""
    storage = Storage(tmp_path / "test.db")
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([make_query()]), storage, FakeKufar({}), notifier
    )

    assert asyncio.run(scheduler.send_lead_digest_if_due()) is False
    assert notifier.texts == []


def test_ai_daily_limit(tmp_path: Path) -> None:
    """После дневного лимита AI не вызывается, работает fallback."""
    query = make_query(notify=True)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ads = [
        make_ad("1", price_byn=10000, subject="серебро"),
        make_ad("2", price_byn=10000, subject="серебро"),
    ]
    analyzer = FakeAnalyzer(ai_verdict(verdict="look"))
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query], llm_max_calls=1),
        storage,
        FakeKufar({query.q: ads}),
        notifier,
        analyzer=analyzer,
    )

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 2
    assert analyzer.calls == 1
    key = f"llm_calls:{datetime.now(timezone.utc).date().isoformat()}"
    assert storage.get_meta(key) == "1"


def test_ai_cache_prevents_repeat_call(tmp_path: Path) -> None:
    """Повторный проход использует кэш AI."""
    query = make_query(notify=True)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=10000, subject="серебро")
    analyzer = FakeAnalyzer(ai_verdict(verdict="look"))
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]),
        storage,
        FakeKufar({query.q: [ad]}),
        notifier,
        analyzer=analyzer,
    )

    asyncio.run(scheduler.run_pass(query))
    asyncio.run(scheduler.run_pass(query))

    assert analyzer.calls == 1
    assert len(notifier.ad_calls) == 1


def stub_vision_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """Не скачивать настоящие картинки в тестах."""
    monkeypatch.setattr(
        scheduler_module,
        "download_images",
        lambda urls, timeout: [("image/jpeg", "ZmFrZQ==")],
    )


def add_detail_image(kufar: FakeKufar, ad: Ad) -> None:
    """Добавить detail-картинку для vision."""
    kufar.detail_images[ad.url] = ["https://img.example/1.jpg"]


def test_vision_false_high_confidence_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vision is_silver=false + stones/другое при confidence>=0.7 — skip."""
    stub_vision_download(monkeypatch)
    query = make_query(notify=False)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=10000, subject="серебро")
    kufar = FakeKufar({query.q: [ad]})
    add_detail_image(kufar, ad)
    vision = FakeVisionAnalyzer(
        vision_verdict(
            is_silver=False,
            item_type="другое",
            stones_or_costume=True,
            confidence=0.9,
        )
    )
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]),
        storage,
        kufar,
        notifier,
        vision_analyzer=vision,
    )

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 0
    assert notifier.ad_calls == []
    assert storage.count_pending() == 0
    assert vision.calls == 1


def test_vision_weight_and_purity_promote_below(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vision вес/проба пересчитывают лом и дают instant below."""
    stub_vision_download(monkeypatch)
    query = make_query(notify=False)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=1000, subject="серебро")
    kufar = FakeKufar({query.q: [ad]})
    add_detail_image(kufar, ad)
    vision = FakeVisionAnalyzer(
        vision_verdict(
            is_silver=True,
            weight_g=10.0,
            purity=925,
            hallmark="зубр",
            confidence=0.95,
        )
    )
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]),
        storage,
        kufar,
        notifier,
        vision_analyzer=vision,
    )

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 1
    assert len(notifier.ad_calls) == 1
    assert notifier.vision_calls[0] is vision.verdict
    saved = storage.get_analysis(ad.ad_id)
    assert saved is not None
    assert saved.weight_g == 10.0
    assert saved.purity == 925
    assert saved.is_below_scrap is True


def test_vision_daily_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """После лимита vision не вызывается, fallback продолжает работать."""
    stub_vision_download(monkeypatch)
    query = make_query(notify=True)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ads = [
        make_ad("1", price_byn=10000, subject="серебро"),
        make_ad("2", price_byn=10000, subject="серебро"),
    ]
    kufar = FakeKufar({query.q: ads})
    for ad in ads:
        add_detail_image(kufar, ad)
    vision = FakeVisionAnalyzer(vision_verdict(weight_g=10.0))
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query], vision_max_calls=1),
        storage,
        kufar,
        notifier,
        vision_analyzer=vision,
    )

    asyncio.run(scheduler.run_pass(query))

    assert vision.calls == 1
    assert len(notifier.ad_calls) == 2
    key = f"vision_calls:{datetime.now(timezone.utc).date().isoformat()}"
    assert storage.get_meta(key) == "1"


def test_vision_cache_prevents_repeat_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кэш ad_vision не вызывает модель повторно."""
    stub_vision_download(monkeypatch)
    query = make_query(notify=True)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=10000, subject="серебро")
    kufar = FakeKufar({query.q: [ad]})
    add_detail_image(kufar, ad)
    vision = FakeVisionAnalyzer(vision_verdict(weight_g=10.0))
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]),
        storage,
        kufar,
        notifier,
        vision_analyzer=vision,
    )

    asyncio.run(scheduler.run_pass(query))
    assert vision.calls == 1
    assert storage.get_vision_verdict(ad.ad_id) is not None

    images = storage.get_ad_detail_images(ad.ad_id)
    analysis = storage.get_analysis(ad.ad_id)
    cached = asyncio.run(
        scheduler._maybe_vision(
            ad,
            query,
            analysis,
            None,
            images,
            default_scrap_prices(),
            allow_api=True,
        )
    )
    assert cached is not None
    assert vision.calls == 1


def test_vision_none_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vision None не блокирует обычный priority-алерт."""
    stub_vision_download(monkeypatch)
    query = make_query(notify=True)
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    ad = make_ad("1", price_byn=10000, subject="серебро")
    kufar = FakeKufar({query.q: [ad]})
    add_detail_image(kufar, ad)
    vision = FakeVisionAnalyzer(None)
    notifier = FakeNotifier()
    scheduler = make_scheduler(
        make_config([query]),
        storage,
        kufar,
        notifier,
        vision_analyzer=vision,
    )

    result = asyncio.run(scheduler.run_pass(query))

    assert result.sent == 1
    assert len(notifier.ad_calls) == 1
    assert notifier.vision_calls == [None]


def test_interval_jitter_bounds() -> None:
    """Джиттер держится в пределах ±20% интервала."""
    delays = [interval_with_jitter(15) for _ in range(100)]

    assert all(12 <= delay <= 18 for delay in delays)


def test_is_older_than_handles_missing_and_bad_time() -> None:
    """Без list_time или с мусором объявление не считается старым."""
    assert is_older_than(make_ad("1"), hours=12) is False
    assert is_older_than(make_ad("2", list_time="не дата"), hours=12) is False


def test_start_and_stop_cancel_tasks(tmp_path: Path) -> None:
    """start создаёт задачи, stop их отменяет."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    scheduler = make_scheduler(
        make_config([query]), storage, FakeKufar({query.q: []}), FakeNotifier()
    )

    async def scenario() -> list[asyncio.Task[None]]:
        tasks = scheduler.start()
        await asyncio.sleep(0.05)
        await scheduler.stop(tasks)
        return tasks

    tasks = asyncio.run(scenario())

    query_tasks = [task for task in tasks if task.get_name().startswith("query:")]
    assert len(query_tasks) == 1
    assert all(task.cancelled() is True for task in tasks)


def test_tracking_false_pauses_and_resumes(tmp_path: Path) -> None:
    """tracking=false не делает fetch; включение подхватывается за секунду."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    control_path = tmp_path / "control.json"
    status_path = tmp_path / "status.json"
    write_control(control_path, ControlState(tracking=False, prevent_sleep=False))
    kufar = FakeKufar({query.q: [make_ad("1")]})
    notifier = FakeNotifier()
    scheduler = Scheduler(  # type: ignore[arg-type]
        make_config([query]),
        kufar,
        storage,
        notifier,
        control_path=control_path,
        status_path=status_path,
        sleep_inhibitor=FakeSleep(),
    )

    async def scenario() -> tuple[list[str], list[str], dict | None]:
        tasks = scheduler.start()
        await asyncio.sleep(1.2)
        calls_before = list(kufar.calls)

        write_control(control_path, ControlState(tracking=True, prevent_sleep=False))
        for _ in range(40):
            await asyncio.sleep(0.1)
            if kufar.calls:
                break
        await asyncio.sleep(0.2)
        calls_after = list(kufar.calls)
        status = read_status(status_path)
        await scheduler.stop(tasks)
        return calls_before, calls_after, status

    before, after, status = asyncio.run(scenario())

    assert before == []
    assert len(after) == 1
    assert len(notifier.ad_calls) == 1
    assert status is not None
    assert status["tracking"] is True


def test_manual_scan_works_with_tracking_false(tmp_path: Path) -> None:
    """Ручной скан идёт при tracking=false и не меняет tracking."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    notifier = FakeNotifier()
    kufar = FakeKufar({query.q: [make_ad("1")]})
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)
    scheduler.tracking = False

    result = asyncio.run(scheduler.run_manual_scan())

    assert result == {"new": 1, "sent": 1}
    assert kufar.calls == [query.q]
    assert scheduler.tracking is False
    assert len(notifier.ad_calls) == 1


def test_manual_scan_does_not_bootstrap(tmp_path: Path) -> None:
    """Ручной скан не запускает bootstrap и не шлёт сводку."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    notifier = FakeNotifier()
    kufar = FakeKufar({query.q: [make_ad("1")]})
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    result = asyncio.run(scheduler.run_manual_scan())

    assert result == {"new": 0, "sent": 0}
    assert kufar.calls == []
    assert notifier.texts == []
    assert storage.is_bootstrapped(query.q) is False


def test_manual_scan_rejects_parallel_run(tmp_path: Path) -> None:
    """Второй параллельный ручной скан отклоняется без второго fetch."""
    query = make_query()
    storage = Storage(tmp_path / "test.db")
    storage.set_bootstrapped(query.q)
    notifier = FakeNotifier()
    kufar = FakeKufar({query.q: [make_ad("1")]}, delay=0.3)
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    async def scenario() -> tuple[dict[str, int], dict[str, int]]:
        first = asyncio.create_task(scheduler.run_manual_scan())
        await asyncio.sleep(0.05)
        second = await scheduler.run_manual_scan()
        return await first, second

    first_result, second_result = asyncio.run(scenario())

    assert first_result == {"new": 1, "sent": 1}
    assert second_result == {"new": 0, "sent": 0}
    assert len(kufar.calls) == 1
