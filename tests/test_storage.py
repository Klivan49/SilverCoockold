"""Тесты SQLite-хранилища: дедупликация, bootstrap, статистика."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from app.models import Ad
from app.storage import Storage


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Путь к временной базе для одного теста."""
    return tmp_path / "test.db"


@pytest.fixture()
def storage(db_path: Path) -> Storage:
    """Хранилище на временной базе."""
    return Storage(db_path)


def make_ad(
    ad_id: str = "1089730241",
    subject: str = "монеты серебро",
    price_byn: int | None = 2499,
    url: str = "https://www.kufar.by/item/1089730241",
) -> Ad:
    """Собрать тестовое объявление."""
    return Ad(
        ad_id=ad_id,
        subject=subject,
        price_byn=price_byn,
        url=url,
        list_time="2026-10-17T07:02:04Z",
        raw={"ad_id": ad_id, "subject": subject, "price_byn": price_byn},
    )


def read_row(db_path: Path, ad_id: str) -> sqlite3.Row:
    """Прочитать объявление напрямую из SQLite."""
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM ads WHERE ad_id = ?", (ad_id,)
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return row


def test_save_and_is_new(storage: Storage, db_path: Path) -> None:
    """Первый save — новое, повторный — нет, дубль не создаётся."""
    ad = make_ad()

    assert storage.is_new(ad.ad_id) is True
    storage.save(ad, "серебро лом")
    assert storage.is_new(ad.ad_id) is False

    storage.save(ad, "серебро лом")
    connection = sqlite3.connect(db_path)
    try:
        count = connection.execute("SELECT COUNT(*) FROM ads").fetchone()[0]
    finally:
        connection.close()
    assert count == 1


def test_saved_fields_and_raw_json(storage: Storage, db_path: Path) -> None:
    """После save subject, цена и raw_json читаются из БД."""
    ad = make_ad(subject="Лот серебра", price_byn=57000)
    storage.save(ad, "серебро лот")

    row = read_row(db_path, ad.ad_id)

    assert row["query"] == "серебро лот"
    assert row["subject"] == "Лот серебра"
    assert row["price_byn"] == 57000
    assert row["url"] == ad.url
    assert row["list_time"] == ad.list_time
    assert json.loads(row["raw_json"]) == ad.raw
    assert "Лот серебра" in row["raw_json"]
    datetime.fromisoformat(row["first_seen"])
    datetime.fromisoformat(row["last_seen"])


def test_bootstrap_flag_survives_new_instance(storage: Storage, db_path: Path) -> None:
    """Флаг bootstrap сохраняется между экземплярами Storage."""
    query = "серебро лом"
    assert storage.is_bootstrapped(query) is False

    storage.set_bootstrapped(query)
    assert storage.is_bootstrapped(query) is True

    another = Storage(db_path)
    assert another.is_bootstrapped(query) is True
    assert another.is_bootstrapped("серебро лот") is False


def test_mark_instant_alerted(storage: Storage, db_path: Path) -> None:
    """mark_instant_alerted меняет флаг с 0 на 1."""
    ad = make_ad()
    storage.save(ad, "серебро лом")
    assert read_row(db_path, ad.ad_id)["instant_alerted"] == 0

    storage.mark_instant_alerted(ad.ad_id)

    assert read_row(db_path, ad.ad_id)["instant_alerted"] == 1


def test_is_instant_alerted_and_meta(storage: Storage) -> None:
    """is_instant_alerted и произвольные ключи meta работают как ожидается."""
    ad = make_ad()
    storage.save(ad, "серебро лом")
    assert storage.is_instant_alerted(ad.ad_id) is False

    storage.mark_instant_alerted(ad.ad_id)
    assert storage.is_instant_alerted(ad.ad_id) is True

    assert storage.get_meta("last_429_notice_at") is None
    storage.set_meta("last_429_notice_at", "2026-10-17T10:00:00+00:00")
    assert storage.get_meta("last_429_notice_at") == "2026-10-17T10:00:00+00:00"
    storage.set_meta("last_429_notice_at", "2026-10-17T10:30:00+00:00")
    assert storage.get_meta("last_429_notice_at") == "2026-10-17T10:30:00+00:00"


def test_stats(storage: Storage) -> None:
    """stats корректно считает объявления по запросам и последний опрос."""
    storage.save(make_ad("1", subject="A"), "серебро лом")
    storage.save(make_ad("2", subject="B"), "серебро лом")
    storage.save(make_ad("3", subject="C"), "серебро лот")
    storage.set_last_poll("серебро лом", "2026-10-17T10:00:00+00:00")
    storage.set_last_poll("серебро лот", "2026-10-17T10:00:15+00:00")

    stats = storage.stats()

    assert stats["ads_total"] == 3
    assert stats["ads_by_query"] == {"серебро лом": 2, "серебро лот": 1}
    assert stats["last_poll"] == {
        "серебро лом": "2026-10-17T10:00:00+00:00",
        "серебро лот": "2026-10-17T10:00:15+00:00",
    }
    assert stats["last_poll_at"] == "2026-10-17T10:00:15+00:00"


def test_last_poll_roundtrip(storage: Storage) -> None:
    """last_poll возвращает None, пока время не сохранено."""
    assert storage.last_poll("серебро лом") is None

    storage.set_last_poll("серебро лом", "2026-10-17T10:00:00+00:00")

    assert storage.last_poll("серебро лом") == "2026-10-17T10:00:00+00:00"


def test_log_alert(storage: Storage, db_path: Path) -> None:
    """log_alert пишет успех и ошибку в таблицу alerts."""
    storage.log_alert("1", "серебро лом", ok=True)
    storage.log_alert("2", "серебро лот", ok=False, error="timeout")

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT ad_id, query, ok, error, sent_at FROM alerts ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    assert len(rows) == 2
    assert rows[0]["ok"] == 1
    assert rows[0]["error"] is None
    assert rows[1]["ok"] == 0
    assert rows[1]["error"] == "timeout"
    datetime.fromisoformat(rows[1]["sent_at"])


def test_wal_mode_and_busy_timeout(storage: Storage, db_path: Path) -> None:
    """Соединения Storage включают WAL и busy_timeout=5000."""
    with storage._connection() as connection:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]

    assert str(mode).lower() == "wal"
    assert busy_timeout == 5000

    # Режим WAL сохраняется в файле и для обычного соединения.
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        connection.close()
