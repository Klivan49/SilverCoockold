"""Фото-анализ объявлений через Claude Haiku (Anthropic API)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.models import Ad
from app.silver import PURITY_VALUES

logger = logging.getLogger(__name__)

DEFAULT_VISION_MODEL = "claude-haiku-5-5"
DEFAULT_VISION_BASE_URL = "https://opencode.ai/inference/go/anthropic/v1"
DEFAULT_SESSION_ID = "ses_silverwatch_ai"
ITEM_TYPES = {"украшение", "лом", "монеты", "столовое", "другое"}
HALLMARKS = {"зубр", "кокошник", "925", "875", "нет", "не видно"}
JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

VISION_PROMPT = """Ты анализируешь фотографии объявления о серебре.
Определи, действительно ли на фото серебряное изделие, а не посеребрение,
бижутерия или одежда. Прочитай вес, если на фото видны весы. Найди клеймо
или пробу. Отметь камни и недрагоценные детали. Если данных мало, задай
один конкретный вопрос продавцу.

Верни СТРОГО JSON без markdown:
{
  "is_silver": true|false,
  "item_type": "украшение|лом|монеты|столовое|другое",
  "weight_g": число 0.1..1000 или null,
  "purity": 999|958|925|916|900|875|830|800|750|500 или null,
  "hallmark": "зубр|кокошник|925|875|нет|не видно",
  "scale_visible": true|false,
  "stones_or_costume": true|false,
  "confidence": число от 0 до 1,
  "notes": "1-2 фразы по-русски",
  "question": "что уточнить у продавца"
}
"""


@dataclass
class VisionVerdict:
    """Вердикт по фотографиям."""

    is_silver: bool
    item_type: str
    weight_g: float | None
    purity: int | None
    hallmark: str
    scale_visible: bool
    stones_or_costume: bool
    confidence: float
    notes: str
    question: str
    model: str


class NullVisionAnalyzer:
    """Заглушка vision-анализатора."""

    async def analyze(
        self, ad: Ad, body: str, images: list[tuple[str, str]]
    ) -> None:
        """Вернуть None: vision выключен."""
        return None


class VisionAnalyzer:
    """Клиент Anthropic Messages API с base64-картинками."""

    def __init__(
        self,
        base_url: str = DEFAULT_VISION_BASE_URL,
        api_key: str = "",
        model: str = DEFAULT_VISION_MODEL,
        timeout: float = 60.0,
        max_images: int = 3,
        session_id: str = DEFAULT_SESSION_ID,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_images = max_images
        self.session_id = session_id
        self._client = client

    def _endpoint(self) -> str:
        """Anthropic Messages endpoint."""
        return f"{self.base_url}/messages"

    def _headers(self) -> dict[str, str]:
        """Заголовки без логирования ключа."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "anthropic-version": "2023-06-01",
            "x-opencode-session": self.session_id,
            "Content-Type": "application/json",
        }

    def _payload(
        self,
        ad: Ad,
        body: str,
        images: list[tuple[str, str]],
    ) -> dict[str, Any]:
        """Собрать сообщение с текстом и base64-картинками."""
        price = (
            f"{ad.price_byn / 100:.2f} BYN"
            if ad.price_byn and ad.price_byn > 0
            else "не указана"
        )
        raw = ad.raw if isinstance(ad.raw, dict) else {}
        prompt = (
            VISION_PROMPT
            + "\n\n"
            + f"Название: {ad.subject or ''}\n"
            + f"Цена: {price}\n"
            + f"Категория: {str(raw.get('category') or '')[:200]}\n"
            + f"Регион: {str(raw.get('region') or '')[:200]}\n"
            + f"Описание: {(body or '')[:2500]}"
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for mime, data in images[: self.max_images]:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": data,
                    },
                }
            )
        return {
            "model": self.model,
            "max_tokens": 1200,
            "messages": [
                {"role": "user", "content": content},
            ],
        }

    async def analyze(
        self,
        ad: Ad,
        body: str,
        images: list[tuple[str, str]],
    ) -> VisionVerdict | None:
        """Запросить vision-вердикт; при ошибке вернуть None."""
        if not self.api_key or not images:
            return None

        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            for attempt in range(2):
                try:
                    response = await client.post(
                        self._endpoint(),
                        headers=self._headers(),
                        json=self._payload(ad, body, images),
                    )
                except (httpx.TimeoutException, httpx.HTTPError) as exc:
                    logger.warning(
                        "vision: ошибка запроса %s", type(exc).__name__
                    )
                    return None

                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == 0:
                        logger.warning(
                            "vision: HTTP %d, повтор", response.status_code
                        )
                        continue
                    logger.warning(
                        "vision: HTTP %d после повтора", response.status_code
                    )
                    return None
                if response.status_code != 200:
                    logger.warning("vision: HTTP %d", response.status_code)
                    return None

                try:
                    payload = response.json()
                    content = self._response_text(payload)
                except (ValueError, TypeError, KeyError, IndexError):
                    logger.warning("vision: неверная структура ответа")
                    return None
                return self._parse_verdict(content)
        finally:
            if own_client:
                await client.aclose()
        return None

    @staticmethod
    def _response_text(payload: Any) -> str:
        """Достать текст из ответа Anthropic или OpenAI-совместимого."""
        if not isinstance(payload, dict):
            return ""
        blocks = payload.get("content")
        if isinstance(blocks, list):
            texts = [
                block.get("text", "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return "\n".join(str(text) for text in texts)
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            return str(choices[0]["message"]["content"])
        return ""

    def _parse_verdict(self, content: str) -> VisionVerdict | None:
        """Разобрать и провалидировать JSON."""
        data = self._extract_json(content)
        if data is None or not isinstance(data.get("is_silver"), bool):
            return None

        item_type = data.get("item_type")
        if item_type not in ITEM_TYPES:
            item_type = "другое"

        weight = data.get("weight_g")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            weight = None
        elif not (0.1 <= float(weight) <= 1000):
            weight = None
        else:
            weight = float(weight)

        purity = data.get("purity")
        if not isinstance(purity, int) or isinstance(purity, bool):
            purity = None
        elif purity not in PURITY_VALUES:
            purity = None

        hallmark = data.get("hallmark")
        if hallmark not in HALLMARKS:
            hallmark = "не видно"

        confidence_raw = data.get("confidence", 0)
        try:
            confidence = min(1.0, max(0.0, float(confidence_raw)))
        except (TypeError, ValueError):
            confidence = 0.0

        notes = data.get("notes")
        question = data.get("question")
        return VisionVerdict(
            is_silver=bool(data["is_silver"]),
            item_type=str(item_type),
            weight_g=weight,
            purity=purity,
            hallmark=str(hallmark),
            scale_visible=bool(data.get("scale_visible", False)),
            stones_or_costume=bool(data.get("stones_or_costume", False)),
            confidence=confidence,
            notes=notes.strip() if isinstance(notes, str) else "",
            question=question.strip() if isinstance(question, str) else "",
            model=self.model,
        )

    @staticmethod
    def _extract_json(content: str) -> dict[str, Any] | None:
        """Вырезать JSON из markdown/текста."""
        if not isinstance(content, str):
            return None
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
        decoder = json.JSONDecoder()
        for match in JSON_RE.finditer(content):
            try:
                parsed, _ = decoder.raw_decode(match.group(0))
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None
