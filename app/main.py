"""CLI-точка входа SilverCoockold."""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.config import BASE_DIR, Config, ConfigError, load_config
from app.control import DEFAULT_CONTROL_PATH
from app.healthcheck import Healthcheck
from app.kufar import KufarClient
from app.notifier import Notifier
from app.scheduler import Scheduler
from app.status import DEFAULT_STATUS_PATH
from app.storage import Storage
from app.systemd_notify import SystemdNotifier
from app.telegram_control import TelegramPoller

LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"


def setup_logging(level: str) -> None:
    """Настроить логирование в консоль и в logs/app.log с ротацией."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)


def mask_secret(value: str | None) -> str:
    """Показать факт наличия секрета, не раскрывая значение."""
    return "***" if value else "not set"


def mask_proxy(url: str | None) -> str:
    """Скрыть логин и пароль в URL прокси."""
    if not url:
        return "not set"
    parts = urlsplit(url)
    if parts.username or parts.password:
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        parts = parts._replace(netloc=f"***@{host}")
    return urlunsplit(parts)


def print_queries(config: Config) -> None:
    """Напечатать запросы, интервалы и флаги из конфига."""
    print(f"Запросы ({len(config.queries)}):")
    for query in config.queries:
        print(
            f"  - «{query.q}»: каждые {query.interval} с, "
            f"instant={'да' if query.instant else 'нет'}, "
            f"notify={'да' if query.notify else 'нет'}"
        )


def check(config: Config) -> int:
    """Проверить конфиг, создать БД со схемой и показать статистику."""
    try:
        storage = Storage(config.db_path)
        ads_total = int(storage.stats()["ads_total"])
        bootstrapped = {
            query.q: storage.is_bootstrapped(query.q) for query in config.queries
        }
    except sqlite3.Error as exc:
        print(f"Ошибка БД {config.db_path}: {exc}", file=sys.stderr)
        return 1

    logging.info("Проверка конфигурации пройдена")

    print("Проверка конфигурации SilverCoockold")
    print_queries(config)
    print(
        f"Лимиты: max_alerts_per_cycle={config.limits.max_alerts_per_cycle}, "
        f"catchup_max_age_hours={config.limits.catchup_max_age_hours:g}, "
        f"request_timeout={config.limits.request_timeout:g} с, "
        f"force_scan_cooldown_sec={config.limits.force_scan_cooldown_sec}"
    )
    print(f"БД: {config.db_path}")
    print(f"Объявлений в базе: {ads_total}")
    print("Bootstrap по запросам:")
    for query in config.queries:
        mark = "да" if bootstrapped[query.q] else "нет"
        print(f"  - «{query.q}»: bootstrapped={mark}")
    print(f"Логи: {LOG_FILE}")
    print(f"Telegram bot token: {mask_secret(config.telegram_token)}")
    print(
        f"Telegram получателей: {len(config.telegram_chat_ids)}; "
        f"админов: {len(config.telegram_admin_ids)}"
    )
    print(f"DRY_RUN: {'1' if config.dry_run else '0'}")
    print(f"LOG_LEVEL: {config.log_level}")
    print(f"PROXY_URL: {mask_proxy(config.proxy_url)}")
    print("OK")
    return 0


def create_components(
    config: Config,
) -> tuple[Scheduler, Notifier, KufarClient, Storage]:
    """Создать Storage, Notifier, KufarClient и Scheduler на один запуск."""
    storage = Storage(config.db_path)
    notifier = Notifier(
        config.telegram_token, config.telegram_chat_ids, config.dry_run
    )
    kufar = KufarClient(
        timeout=config.limits.request_timeout, proxy_url=config.proxy_url
    )
    control_path = Path(os.getenv("CONTROL_PATH") or DEFAULT_CONTROL_PATH)
    status_path = Path(os.getenv("STATUS_PATH") or DEFAULT_STATUS_PATH)
    scheduler = Scheduler(
        config,
        kufar,
        storage,
        notifier,
        control_path=control_path,
        status_path=status_path,
    )
    return scheduler, notifier, kufar, storage


def run_once(config: Config) -> int:
    """Один проход по всем запросам, включая bootstrap."""
    logging.info("Режим --once: один проход по всем запросам")
    print("Один проход по всем запросам")
    scheduler, notifier, kufar, _storage = create_components(config)

    async def _run() -> None:
        try:
            for query in config.queries:
                await scheduler.run_pass(query)
        finally:
            await notifier.close()
            kufar.close()

    asyncio.run(_run())
    print("Готово")
    return 0


def install_signal_handlers(
    loop: asyncio.AbstractEventLoop, stop: asyncio.Event
) -> None:
    """Повесить SIGINT/SIGTERM; на Windows add_signal_handler не работает."""
    def request_stop(*_: object) -> None:
        loop.call_soon_threadsafe(stop.set)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
            continue
        except (NotImplementedError, RuntimeError):
            pass
        try:
            signal.signal(sig, request_stop)
        except (ValueError, OSError):
            logging.debug("Не удалось установить обработчик сигнала %s", sig)


def run_daemon(config: Config) -> int:
    """Рабочий режим: циклы, Telegram polling и выход по сигналу."""
    scheduler, notifier, kufar, storage = create_components(config)
    poller = TelegramPoller(
        config.telegram_token,
        config.telegram_chat_ids,
        scheduler,
        storage,
        config.limits.force_scan_cooldown_sec,
        admin_ids=config.telegram_admin_ids,
    )
    healthcheck = Healthcheck(config.healthcheck_url, scheduler)

    async def _run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        install_signal_handlers(loop, stop)

        tasks = scheduler.start()
        poller_task: asyncio.Task[None] | None = None
        if not config.dry_run and config.telegram_token and config.telegram_chat_ids:
            poller_task = asyncio.create_task(poller.run(), name="telegram")
        else:
            logging.info(
                "Telegram poller не запущен: DRY_RUN или нет токена/получателей"
            )
        healthcheck_task: asyncio.Task[None] | None = None
        if config.healthcheck_url:
            healthcheck_task = asyncio.create_task(
                healthcheck.run(), name="healthcheck"
            )
        systemd_task = asyncio.create_task(
            SystemdNotifier().run(), name="systemd-notify"
        )

        print("Демон запущен. Для выхода нажмите Ctrl+C.")
        try:
            await stop.wait()
        finally:
            if poller_task is not None:
                poller_task.cancel()
                await asyncio.gather(poller_task, return_exceptions=True)
            if healthcheck_task is not None:
                healthcheck_task.cancel()
                await asyncio.gather(healthcheck_task, return_exceptions=True)
            systemd_task.cancel()
            await asyncio.gather(systemd_task, return_exceptions=True)
            await poller.close()
            await scheduler.stop(tasks)
            await notifier.close()
            kufar.close()

    asyncio.run(_run())
    print("Остановлено.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Разобрать аргументы командной строки и запустить нужный режим."""
    parser = argparse.ArgumentParser(
        prog="python -m app",
        description="SilverCoockold — мониторинг объявлений Kufar.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check", action="store_true", help="проверить конфиг, папки и доступ к БД"
    )
    mode.add_argument(
        "--once", action="store_true", help="один проход по всем запросам"
    )
    mode.add_argument(
        "--daemon", action="store_true", help="рабочий режим с циклами опроса"
    )
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 1

    setup_logging(config.log_level)

    if args.check:
        return check(config)
    if args.once:
        return run_once(config)
    return run_daemon(config)
