#!/usr/bin/env python3
"""Скачать один реальный ответ Kufar и сохранить его как фикстуру.

Запуск вручную из корня проекта:
    .venv/bin/python scripts/fetch_fixture.py "серебро лом"

По умолчанию файл пишется в tests/fixtures/<slug>.json:
    «серебро лом» → tests/fixtures/search_lom.json
    «серебро лот» → tests/fixtures/search_lot.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

# Чтобы работал импорт app при запуске из scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.kufar import API_URL, USER_AGENT  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
TIMEOUT = 20.0
PAGE_SIZE = 100
KNOWN_SLUGS = {
    "серебро лом": "search_lom",
    "серебро лот": "search_lot",
}
TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def fixture_slug(query: str) -> str:
    """Вернуть имя фикстуры: известные запросы — search_lom/search_lot."""
    normalized = query.strip().lower()
    if normalized in KNOWN_SLUGS:
        return KNOWN_SLUGS[normalized]
    transliterated = "".join(TRANSLIT.get(char, char) for char in normalized)
    slug = re.sub(r"[^a-z0-9]+", "_", transliterated).strip("_")
    return slug or "query"


def fetch_once(query: str) -> dict[str, Any]:
    """Сделать один запрос к Kufar: первая страница, size=100, без token."""
    response = httpx.get(
        API_URL,
        params={"query": query, "size": PAGE_SIZE},
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9",
        },
        timeout=TIMEOUT,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Kufar вернул не объект JSON")
    return payload


def main() -> int:
    """Запросить Kufar и сохранить ответ в файл фикстуры."""
    parser = argparse.ArgumentParser(description="Сохранить ответ Kufar в фикстуру")
    parser.add_argument("query", help="поисковый запрос, например «серебро лом»")
    parser.add_argument(
        "--out",
        type=Path,
        help="путь к JSON-файлу (по умолчанию tests/fixtures/<slug>.json)",
    )
    args = parser.parse_args()

    try:
        payload = fetch_once(args.query)
    except httpx.HTTPStatusError as exc:
        print(f"Kufar вернул HTTP {exc.response.status_code}", file=sys.stderr)
        return 1
    except (httpx.HTTPError, ValueError) as exc:
        print(f"Не удалось получить ответ Kufar: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    ads = payload.get("ads")
    if not isinstance(ads, list):
        print("В ответе Kufar нет списка ads", file=sys.stderr)
        return 1

    out_path = args.out or FIXTURES_DIR / f"{fixture_slug(args.query)}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )

    print(f"total={payload.get('total')} saved={len(ads)} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
