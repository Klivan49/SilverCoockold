#!/usr/bin/env python3
"""Скачать один реальный ответ Kufar и сохранить его как фикстуру.

Запуск вручную из корня проекта:
    .venv/bin/python scripts/fetch_fixture.py "серебро лом" tests/fixtures/search_lom.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

# Чтобы работал импорт app при запуске из scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.kufar import API_URL, USER_AGENT  # noqa: E402


def fetch_once(query: str) -> dict:
    """Сделать один запрос; на 429 подождать 30 секунд и повторить."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    params = {"query": query, "size": 100}

    for attempt in range(2):
        response = httpx.get(
            API_URL,
            params=params,
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        )
        if response.status_code == 429 and attempt == 0:
            print("Kufar вернул 429, ждём 30 секунд и пробуем ещё раз...")
            time.sleep(30)
            continue
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Kufar вернул не объект JSON")
        return payload

    raise RuntimeError("не удалось получить ответ Kufar")


def main() -> int:
    parser = argparse.ArgumentParser(description="Сохранить фикстуру Kufar")
    parser.add_argument("query", help="поисковый запрос, например «серебро лом»")
    parser.add_argument("out", help="путь к JSON-файлу фикстуры")
    parser.add_argument("--limit", type=int, default=10, help="сколько ads сохранить")
    args = parser.parse_args()

    try:
        payload = fetch_once(args.query)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    ads = payload.get("ads")
    if not isinstance(ads, list):
        print("Ошибка: в ответе нет списка ads", file=sys.stderr)
        return 1

    payload["ads"] = ads[: args.limit]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    print(
        f"total={payload.get('total')} "
        f"saved={len(payload['ads'])} -> {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
