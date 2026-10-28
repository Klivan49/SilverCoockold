"""AI-фильтр объявлений через OpenAI-совместимый API."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

from app.analyzers.base import AiVerdict
from app.models import Ad
from app.scrap import ScrapPrices
from app.silver import SilverAnalysis

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
BODY_LIMIT = 2500
CATEGORIES = {"украшение", "лом", "монеты", "столовое", "другое"}
VERDICTS = {"look", "skip", "ask"}
JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM_PROMPT = """Ты фильтр объявлений о серебре для покупателя лома.
Реши, похоже ли объявление на серебро и стоит ли его смотреть.
Отсеивай одежду и обувь, «серебряный цвет», бижутерию, посеребрение,
мельхиор, латунь и другие имитации. Не считай серебром просто слово
«серебро» в названии цвета.

is_silver=true, если в объявлении заявлено серебро или оно похоже на
серебро, даже когда вес и проба не указаны. is_silver=false только
для явно не серебряных вещей: одежды, цвета, бижутерии, посеребрения,
латуни, мельхиора.
verdict=skip только для явно не серебряных или бесполезных лотов.
verdict=ask, если это похоже на серебро, но не хватает веса/пробы/клейма.
verdict=look, если данных достаточно и лот стоит внимания.

Извлеки вес в граммах, пробу и пойми, указана ли цена за грамм.
Если данных не хватает, задай один конкретный вопрос продавцу.
Не придумывай вес или пробу.

Верни СТРОГО JSON-объект без markdown:
{
  "is_silver": true|false,
  "category": "украшение|лом|монеты|столовое|другое",
  "verdict": "look|skip|ask",
  "reason": "1-2 фразы по-русски, почему смотреть или пропустить",
  "question": "что спросить у продавца, если данных мало, иначе пустая строка",
  "weight_g": число или null,
  "purity": целое число или null,
  "confidence": число от 0 до 1,
  "unprofitable_prob": число 0..1 — вероятность, что покупать этот лот по
    этой цене невыгодно. Учитывай цену за грамм против лома, отсутствие
    веса/пробы/клейма, посеребрение/бижутерию, камни и недрагоценные детали,
    состояние, комплектность, доставку/пересылку и риск подделки. 0 — явно
    выгодно, 1 — явно невыгодно. Это не confidence (уверенность в
    классификации).
}
"""


class DeepSeekAnalyzer:
    """Клиент /chat/completions с жёстким JSON-ответом и fallback."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        timeout: float = 20.0,
        session_id: str = "ses_silverwatch_ai",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.session_id = session_id
        self._client = client

    def _endpoint(self) -> str:
        """URL OpenAI-совместимого chat/completions."""
        return f"{self.base_url}/chat/completions"

    def _payload(
        self, ad: Ad, body: str, regex_analysis: SilverAnalysis, prices: ScrapPrices
    ) -> dict[str, Any]:
        """Собрать запрос без персональных данных продавца."""
        raw = ad.raw if isinstance(ad.raw, dict) else {}
        category = raw.get("category")
        region = raw.get("region")
        price = (
            f"{ad.price_byn / 100:.2f} BYN"
            if ad.price_byn and ad.price_byn > 0
            else "не указана"
        )
        user_data = {
            "title": ad.subject or "",
            "body": (body or "")[:BODY_LIMIT],
            "price": price,
            "category": str(category)[:200] if category is not None else "",
            "region": str(region)[:200] if region is not None else "",
        }
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(user_data, ensure_ascii=False),
                },
            ],
            "temperature": 0,
            "max_tokens": 900,
            "response_format": {"type": "json_object"},
        }

    def _headers(self) -> dict[str, str]:
        """Заголовки запроса; ключ не логируется."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "x-opencode-session": self.session_id,
        }

    async def analyze(
        self,
        ad: Ad,
        body: str,
        regex_analysis: SilverAnalysis,
        prices: ScrapPrices,
    ) -> AiVerdict | None:
        """Запросить вердикт; при любой ошибке вернуть None."""
        if not self.api_key:
            logger.warning("AI: ключ не задан, используется fallback")
            return None

        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        payload = self._payload(ad, body, regex_analysis, prices)
        try:
            for attempt in range(3):
                try:
                    response = await client.post(
                        self._endpoint(),
                        headers=self._headers(),
                        json=payload,
                    )
                except (httpx.TimeoutException, httpx.HTTPError) as exc:
                    if attempt < 2:
                        delay = 2 * (attempt + 1)
                        logger.warning(
                            "AI: ошибка запроса %s, повтор через %d с",
                            type(exc).__name__,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.warning(
                        "AI: ошибка запроса %s после повторов",
                        type(exc).__name__,
                    )
                    return None

                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        delay = 2 * (attempt + 1)
                        logger.warning(
                            "AI: HTTP %d, повтор через %d с",
                            response.status_code,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.warning(
                        "AI: HTTP %d после повторов", response.status_code
                    )
                    return None
                if response.status_code != 200:
                    logger.warning("AI: HTTP %d", response.status_code)
                    return None

                try:
                    response_payload = response.json()
                    content = response_payload["choices"][0]["message"]["content"]
                except (ValueError, KeyError, IndexError, TypeError):
                    logger.warning("AI: неверная структура ответа")
                    return None

                return self._parse_verdict(content)
        finally:
            if own_client:
                await client.aclose()

        return None

    def _parse_verdict(self, content: Any) -> AiVerdict | None:
        """Разобрать JSON из content и провалидировать поля."""
        if not isinstance(content, str):
            return None
        data = self._extract_json(content)
        if data is None:
            return None
        if not isinstance(data.get("is_silver"), bool):
            return None
        verdict = data.get("verdict")
        if verdict not in VERDICTS:
            return None
        category = data.get("category")
        if category not in CATEGORIES:
            category = "другое"

        weight_raw = data.get("weight_g")
        weight = None
        if isinstance(weight_raw, (int, float)) and not isinstance(
            weight_raw, bool
        ) and weight_raw > 0:
            weight = float(weight_raw)

        purity_raw = data.get("purity")
        purity = None
        if isinstance(purity_raw, (int, float)) and not isinstance(
            purity_raw, bool
        ) and purity_raw > 0:
            purity = int(purity_raw)

        confidence_raw = data.get("confidence", 0)
        try:
            confidence = min(1.0, max(0.0, float(confidence_raw)))
        except (TypeError, ValueError):
            confidence = 0.0

        prob_raw = data.get("unprofitable_prob", 0)
        try:
            unprofitable_prob = min(1.0, max(0.0, float(prob_raw)))
        except (TypeError, ValueError):
            unprofitable_prob = 0.0

        reason = data.get("reason")
        question = data.get("question")
        return AiVerdict(
            is_silver=bool(data["is_silver"]),
            category=str(category),
            verdict=str(verdict),
            reason=reason.strip() if isinstance(reason, str) else "",
            question=question.strip() if isinstance(question, str) else "",
            weight_g=weight,
            purity=purity,
            confidence=confidence,
            model=self.model,
            unprofitable_prob=unprofitable_prob,
        )

    @staticmethod
    def _extract_json(content: str) -> dict[str, Any] | None:
        """Достать JSON-объект, даже если модель добавила текст вокруг."""
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
