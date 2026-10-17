#!/usr/bin/env python3
"""Разовый пересчёт веса/пробы/цены лома для уже сохранённых объявлений.

Скрипт не трогает ads и не рассылает отдельные алерты: он заполняет
ad_details/ad_analysis и в конце отправляет один дайджест «ниже лома».

Запуск (сервис лучше остановить, чтобы не конкурировать за Kufar):
    .venv/bin/python scripts/backfill_analysis.py --limit 3 --no-digest
    .venv/bin/python scripts/backfill_analysis.py
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import random
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_config  # noqa: E402
from app.kufar import KufarClient, KufarError  # noqa: E402
from app.models import Ad  # noqa: E402
from app.notifier import Notifier  # noqa: E402
from app.scrap import load_scrap_prices  # noqa: E402
from app.silver import SilverAnalysis, analyze_ad  # noqa: E402
from app.storage import Storage  # noqa: E402


def load_rows(db_path: Path) -> list[sqlite3.Row]:
    """Прочитать все объявления из ads."""
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT ad_id, subject, price_byn, url, list_time, raw_json "
            "FROM ads ORDER BY first_seen"
        ).fetchall()
    finally:
        connection.close()


def format_digest(
    checked: int, below: list[tuple[Ad, SilverAnalysis]], errors: int
) -> str:
    """Собрать одно сообщение с итогами бэкфилла."""
    lines = [
        f"📊 Бэкфилл анализа: проверено {checked}, ниже лома {len(below)}, "
        f"ошибок {errors}"
    ]
    for ad, analysis in below[:10]:
        subject = html.escape((ad.subject or "")[:80], quote=False)
        url = html.escape(ad.url, quote=False)
        if analysis.price_per_gram is None or analysis.weight_g is None:
            continue
        scrap_per_gram = (
            analysis.scrap_price / analysis.weight_g
            if analysis.scrap_price is not None
            else None
        )
        purity = analysis.effective_purity or analysis.purity or "—"
        line = (
            f"{subject} — {analysis.price_per_gram:.2f} BYN/г, "
            f"{analysis.weight_g:g} г, {purity}"
        )
        if scrap_per_gram is not None:
            line += f" (лом {scrap_per_gram:.2f})"
        lines.append(line)
        lines.append(url)
    if len(below) > 10:
        lines.append(f"…и ещё {len(below) - 10}.")
    return "\n".join(lines)


async def send_digest(text: str, token: str | None, chat_ids: list[str]) -> None:
    """Отправить дайджест всем получателям."""
    if not token or not chat_ids:
        print("Дайджест не отправлен: нет токена или получателей")
        return
    notifier = Notifier(token, chat_ids, dry_run=False)
    try:
        await notifier.send_text(text)
    finally:
        await notifier.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Бэкфилл анализа серебра")
    parser.add_argument("--limit", type=int, default=0, help="обработать только N")
    parser.add_argument("--delay", type=float, default=1.2, help="пауза между detail")
    parser.add_argument("--no-digest", action="store_true", help="не отправлять итог")
    args = parser.parse_args()

    config = load_config()
    storage = Storage(config.db_path)
    prices = load_scrap_prices()
    rows = load_rows(config.db_path)
    if args.limit:
        rows = rows[: args.limit]

    client = KufarClient(
        timeout=config.limits.request_timeout,
        proxy_url=config.proxy_url,
    )
    below: list[tuple[Ad, SilverAnalysis]] = []
    errors = 0
    started = time.monotonic()

    try:
        for index, row in enumerate(rows, 1):
            ad_id = str(row["ad_id"])
            body = storage.get_ad_detail(ad_id)
            if not body:
                try:
                    body = client.fetch_detail(row["url"])
                    storage.save_ad_detail(ad_id, body)
                except KufarError as exc:
                    errors += 1
                    print(f"[{index}/{len(rows)}] detail error {ad_id}: {exc}")
                    if errors % 3 == 0:
                        print("3 ошибки подряд/всего — пауза 30 с")
                        time.sleep(30)
                    continue

            raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
            ad = Ad(
                ad_id=ad_id,
                subject=row["subject"] or "",
                price_byn=row["price_byn"],
                url=row["url"],
                list_time=row["list_time"],
                raw=raw,
            )
            analysis = analyze_ad(ad, body, prices)
            storage.save_analysis(ad_id, analysis)
            if analysis.is_below_scrap:
                below.append((ad, analysis))

            if index % 10 == 0 or index == len(rows):
                elapsed = int(time.monotonic() - started)
                print(
                    f"[{index}/{len(rows)}] ниже лома={len(below)} "
                    f"ошибок={errors} прошло={elapsed}s",
                    flush=True,
                )
            time.sleep(max(0.0, args.delay + random.uniform(0.0, 0.3)))
    finally:
        client.close()

    print(
        f"Готово: проверено {len(rows)}, ниже лома {len(below)}, ошибок {errors}",
        flush=True,
    )
    if not args.no_digest:
        text = format_digest(len(rows), below, errors)
        asyncio.run(send_digest(text, config.telegram_token, config.telegram_chat_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
