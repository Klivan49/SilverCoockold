"""Хранилище SQLite: объявления, алерты и служебные флаги.

Соединение открывается на каждый вызов: так проще и безопаснее для
вызовов из разных потоков (планировщик может уходить в asyncio.to_thread),
а WAL и busy_timeout позволяют пережить редкие одновременные записи.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.models import Ad
from app.silver import SilverAnalysis

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ads (
  ad_id TEXT PRIMARY KEY,
  query TEXT NOT NULL,
  subject TEXT NOT NULL,
  price_byn INTEGER,
  url TEXT NOT NULL,
  list_time TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  raw_json TEXT,
  instant_alerted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ads_first_seen ON ads(first_seen);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ad_id TEXT NOT NULL,
  query TEXT NOT NULL,
  sent_at TEXT NOT NULL,
  ok INTEGER NOT NULL,
  error TEXT
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ad_details (
  ad_id TEXT PRIMARY KEY,
  body TEXT,
  fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ad_analysis (
  ad_id TEXT PRIMARY KEY,
  weight_g REAL,
  purity INTEGER,
  effective_purity INTEGER,
  price_byn REAL,
  price_per_gram REAL,
  scrap_price REAL,
  is_below_scrap INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  parsed_at TEXT NOT NULL
);
"""


def utc_now_iso() -> str:
    """Текущее время UTC в формате ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


class Storage:
    """Простое SQLite-хранилище без ORM и без миграций."""

    def __init__(self, db_path: str | Path) -> None:
        """Открыть (при необходимости создать) базу и таблицы."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Открыть соединение, закоммитить при успехе и закрыть."""
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_schema(self) -> None:
        """Создать таблицы и индекс, если их ещё нет."""
        with self._connection() as connection:
            connection.executescript(SCHEMA_SQL)

    def is_new(self, ad_id: str) -> bool:
        """Проверить, встречалось ли объявление раньше."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM ads WHERE ad_id = ?", (ad_id,)
            ).fetchone()
        return row is None

    def save(self, ad: Ad, query: str) -> None:
        """Сохранить объявление; повторный вызов обновляет last_seen."""
        now = utc_now_iso()
        raw_json = json.dumps(ad.raw, ensure_ascii=False)
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO ads ("
                "ad_id, query, subject, price_byn, url, list_time, "
                "first_seen, last_seen, raw_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ad.ad_id,
                    query,
                    ad.subject,
                    ad.price_byn,
                    ad.url,
                    ad.list_time,
                    now,
                    now,
                    raw_json,
                ),
            )
            connection.execute(
                "UPDATE ads SET last_seen = ? WHERE ad_id = ?", (now, ad.ad_id)
            )

    def is_instant_alerted(self, ad_id: str) -> bool:
        """Проверить, было ли уже отправлено мгновенное уведомление."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT instant_alerted FROM ads WHERE ad_id = ?", (ad_id,)
            ).fetchone()
        return bool(row is not None and row["instant_alerted"])

    def mark_instant_alerted(self, ad_id: str) -> None:
        """Пометить, что мгновенное уведомление по объявлению отправлено."""
        with self._connection() as connection:
            connection.execute(
                "UPDATE ads SET instant_alerted = 1 WHERE ad_id = ?", (ad_id,)
            )

    def is_bootstrapped(self, query: str) -> bool:
        """Проверить, выполнен ли первичный проход по запросу."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM meta WHERE key = ?", (f"bootstrapped:{query}",)
            ).fetchone()
        return row is not None

    def set_bootstrapped(self, query: str) -> None:
        """Зафиксировать, что первичный проход по запросу выполнен."""
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, '1') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"bootstrapped:{query}",),
            )

    def log_alert(
        self, ad_id: str, query: str, ok: bool, error: str | None = None
    ) -> None:
        """Записать результат отправки уведомления."""
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO alerts (ad_id, query, sent_at, ok, error) "
                "VALUES (?, ?, ?, ?, ?)",
                (ad_id, query, utc_now_iso(), int(ok), error),
            )

    def last_poll(self, query: str) -> str | None:
        """Вернуть время последнего успешного опроса запроса."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = ?", (f"last_poll:{query}",)
            ).fetchone()
        return None if row is None else str(row["value"])

    def set_last_poll(self, query: str, ts: str) -> None:
        """Сохранить время последнего успешного опроса запроса."""
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"last_poll:{query}", ts),
            )

    def get_ad_detail(self, ad_id: str) -> str | None:
        """Прочитать сохранённое описание объявления."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT body FROM ad_details WHERE ad_id = ?", (ad_id,)
            ).fetchone()
        return None if row is None else str(row["body"] or "")

    def save_ad_detail(self, ad_id: str, body: str) -> None:
        """Сохранить описание объявления и время загрузки."""
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO ad_details (ad_id, body, fetched_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(ad_id) DO UPDATE SET "
                "body = excluded.body, fetched_at = excluded.fetched_at",
                (ad_id, body, utc_now_iso()),
            )

    def get_analysis(self, ad_id: str) -> SilverAnalysis | None:
        """Прочитать сохранённый разбор объявления."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM ad_analysis WHERE ad_id = ?", (ad_id,)
            ).fetchone()
        if row is None:
            return None
        note = str(row["note"] or "")
        price_byn = row["price_byn"]
        price_per_gram = row["price_per_gram"]
        per_gram = "цена за грамм" in note or (
            price_byn is not None
            and price_per_gram is not None
            and abs(float(price_byn) - float(price_per_gram)) < 1e-9
        )
        return SilverAnalysis(
            weight_g=row["weight_g"],
            purity=row["purity"],
            effective_purity=row["effective_purity"],
            price_byn=price_byn,
            price_per_gram=price_per_gram,
            scrap_price=row["scrap_price"],
            is_below_scrap=bool(row["is_below_scrap"]),
            per_gram_pricing=per_gram,
            note=note,
        )

    def save_analysis(self, ad_id: str, analysis: SilverAnalysis) -> None:
        """Сохранить разбор объявления."""
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO ad_analysis ("
                "ad_id, weight_g, purity, effective_purity, price_byn, "
                "price_per_gram, scrap_price, is_below_scrap, note, parsed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(ad_id) DO UPDATE SET "
                "weight_g = excluded.weight_g, purity = excluded.purity, "
                "effective_purity = excluded.effective_purity, "
                "price_byn = excluded.price_byn, "
                "price_per_gram = excluded.price_per_gram, "
                "scrap_price = excluded.scrap_price, "
                "is_below_scrap = excluded.is_below_scrap, "
                "note = excluded.note, parsed_at = excluded.parsed_at",
                (
                    ad_id,
                    analysis.weight_g,
                    analysis.purity,
                    analysis.effective_purity,
                    analysis.price_byn,
                    analysis.price_per_gram,
                    analysis.scrap_price,
                    int(analysis.is_below_scrap),
                    analysis.note,
                    utc_now_iso(),
                ),
            )

    def get_meta(self, key: str) -> str | None:
        """Прочитать произвольное значение из meta."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        """Записать произвольное значение в meta."""
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def delete_meta(self, key: str) -> None:
        """Удалить значение из meta."""
        with self._connection() as connection:
            connection.execute("DELETE FROM meta WHERE key = ?", (key,))

    def stats(self) -> dict[str, object]:
        """Вернуть количество объявлений по запросам и время опросов."""
        with self._connection() as connection:
            total = connection.execute("SELECT COUNT(*) FROM ads").fetchone()[0]
            by_query = {
                str(row["query"]): int(row["count"])
                for row in connection.execute(
                    "SELECT query, COUNT(*) AS count FROM ads "
                    "GROUP BY query ORDER BY query"
                )
            }
            polls = {
                str(row["key"]).removeprefix("last_poll:"): str(row["value"])
                for row in connection.execute(
                    "SELECT key, value FROM meta "
                    "WHERE key LIKE 'last_poll:%' ORDER BY key"
                )
            }
        return {
            "ads_total": int(total),
            "ads_by_query": by_query,
            "last_poll": polls,
            "last_poll_at": max(polls.values()) if polls else None,
        }
