"""CLI-точка входа SilverCoockold."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sqlite3
import sys
import time
from urllib.parse import urlsplit, urlunsplit

from app.config import BASE_DIR, Config, ConfigError, load_config
from app.storage import Storage

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
        f"request_timeout={config.limits.request_timeout:g} с"
    )
    print(f"БД: {config.db_path}")
    print(f"Объявлений в базе: {ads_total}")
    print("Bootstrap по запросам:")
    for query in config.queries:
        mark = "да" if bootstrapped[query.q] else "нет"
        print(f"  - «{query.q}»: bootstrapped={mark}")
    print(f"Логи: {LOG_FILE}")
    print(f"Telegram bot token: {mask_secret(config.telegram_token)}")
    print(f"Telegram chat_id: {mask_secret(config.telegram_chat_id)}")
    print(f"DRY_RUN: {'1' if config.dry_run else '0'}")
    print(f"LOG_LEVEL: {config.log_level}")
    print(f"PROXY_URL: {mask_proxy(config.proxy_url)}")
    print("OK")
    return 0


def run_once(config: Config) -> int:
    """Один проход по запросам: заглушка шага 1, без сети."""
    logging.info("Режим --once (заглушка шага 1, сеть не используется)")
    print("Один проход по запросам (заглушка шага 1, сеть не используется)")
    print_queries(config)
    print(f"Готово: запросов {len(config.queries)}")
    return 0


def run_daemon(config: Config) -> int:
    """Рабочий режим: заглушка шага 1, выход по Ctrl+C."""
    logging.info("Демон запущен (заглушка шага 1)")
    print("Демон запущен в режиме заглушки. Для выхода нажмите Ctrl+C.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Получен сигнал остановки, выход")
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
        "--once", action="store_true", help="один проход по всем запросам (заглушка)"
    )
    mode.add_argument(
        "--daemon", action="store_true", help="рабочий режим (заглушка)"
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
