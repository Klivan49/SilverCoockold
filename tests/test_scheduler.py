"""Тесты планировщика: bootstrap, дедупликация, лимит, catchup и 429."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import Config, LimitsConfig, QueryConfig
from app.control import ControlState, write_control
from app.kufar import KufarError
from app.models import Ad
from app.scheduler import (
    ERROR_429_TEXT,
    LAST_429_NOTICE_KEY,
    Scheduler,
    interval_with_jitter,
    is_older_than,
    search_url,
)
from app.status import read_status
from app.storage import Storage


class FakeKufar:
    """Подделка KufarClient: отдаёт заранее заданные ответы без сети."""

    def __init__(
        self,
        responses: dict[str, list[Ad] | Exception],
        delay: float = 0.0,
    ) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.delay = delay

    def fetch(self, query: str) -> list[Ad]:
        self.calls.append(query)
        if self.delay:
            time.sleep(self.delay)
        response = self.responses.get(query, [])
        if isinstance(response, Exception):
            raise response
        return list(response)


class FakeNotifier:
    """Подделка Notifier: записывает вызовы, ничего не отправляет."""

    def __init__(self) -> None:
        self.ad_calls: list[tuple[Ad, str]] = []
        self.texts: list[str] = []
        self.closed = False

    async def send_ad(self, ad: Ad, query: str) -> bool:
        self.ad_calls.append((ad, query))
        return True

    async def send_text(self, text: str) -> bool:
        self.texts.append(text)
        return True

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
) -> Config:
    """Собрать конфиг для тестов без чтения файлов и env."""
    return Config(
        queries=queries,
        limits=LimitsConfig(
            max_alerts_per_cycle=max_alerts,
            catchup_max_age_hours=catchup_hours,
            request_timeout=20,
        ),
        telegram_token=None,
        telegram_chat_id=None,
        db_path=Path("unused.db"),
        dry_run=True,
        log_level="INFO",
        proxy_url=None,
    )


def make_ad(ad_id: str = "1", list_time: str | None = None) -> Ad:
    """Собрать тестовое объявление."""
    return Ad(
        ad_id=ad_id,
        subject=f"объявление {ad_id}",
        price_byn=2499,
        url=f"https://www.kufar.by/item/{ad_id}",
        list_time=list_time,
        raw={"ad_id": ad_id},
    )


def make_scheduler(
    config: Config,
    storage: Storage,
    kufar: FakeKufar,
    notifier: FakeNotifier,
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
        sleep_inhibitor=FakeSleep(),
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
    kufar = FakeKufar({query.q: KufarError("HTTP 429")})
    notifier = FakeNotifier()
    scheduler = make_scheduler(make_config([query]), storage, kufar, notifier)

    first = asyncio.run(scheduler.run_pass(query))
    second = asyncio.run(scheduler.run_pass(query))

    assert first.paused is True
    assert second.paused is True
    assert notifier.texts.count(ERROR_429_TEXT) == 1
    assert storage.get_meta(LAST_429_NOTICE_KEY) is not None

    kufar.responses[query.q] = [make_ad("1")]
    third = asyncio.run(scheduler.run_pass(query))

    assert third.paused is False
    assert third.sent == 1
    assert len(notifier.ad_calls) == 1


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
