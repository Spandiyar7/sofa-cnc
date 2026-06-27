"""Анализ фото дивана через Claude Vision API.

Если ключ ANTHROPIC_API_KEY не задан или Claude недоступен — возвращаем
конфигурацию по умолчанию с пометкой "manual", чтобы оператор заполнил форму
вручную (см. требование "Если Claude Vision не смог распознать").
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re

logger = logging.getLogger("sofa-cnc.vision")

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

SYSTEM_PROMPT = (
    "Ты — эксперт по конструкции мягкой мебели. Проанализируй фото дивана "
    "и верни ТОЛЬКО JSON без пояснений:\n\n"
    "{\n"
    '  "type": "straight" | "corner_l" | "corner_u" | "armchair",\n'
    '  "armrests": "both" | "left" | "right" | "none",\n'
    '  "backrest": "straight" | "angled" | "reclining",\n'
    '  "chaise": true | false,\n'
    '  "chaise_side": "left" | "right" | null,\n'
    '  "sections": 1,\n'
    '  "style": "modern" | "classic" | "scandinavian",\n'
    '  "legs_visible": true | false,\n'
    '  "estimated_seats": 2\n'
    "}\n\n"
    "Отвечай только валидным JSON. Никакого текста до или после."
)

DEFAULT_CONFIG = {
    "type": "straight",
    "armrests": "both",
    "backrest": "straight",
    "chaise": False,
    "chaise_side": None,
    "sections": 1,
    "style": "modern",
    "legs_visible": True,
    "estimated_seats": 2,
}

_ALLOWED = {
    "type": {"straight", "corner_l", "corner_u", "armchair"},
    "armrests": {"both", "left", "right", "none"},
    "backrest": {"straight", "angled", "reclining"},
    "style": {"modern", "classic", "scandinavian"},
}


def _extract_json(text: str) -> dict:
    """Достаём JSON даже если Claude обернул его в ```json ... ```."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    return json.loads(text)


def _sanitize(raw: dict) -> dict:
    """Сводим ответ модели к нашей схеме, отбрасывая мусор."""
    cfg = dict(DEFAULT_CONFIG)
    for key, default in DEFAULT_CONFIG.items():
        if key not in raw:
            continue
        val = raw[key]
        if key in _ALLOWED and val not in _ALLOWED[key]:
            continue
        cfg[key] = val
    try:
        cfg["sections"] = max(1, int(raw.get("sections", 1)))
    except (TypeError, ValueError):
        cfg["sections"] = 1
    try:
        cfg["estimated_seats"] = max(1, int(raw.get("estimated_seats", 2)))
    except (TypeError, ValueError):
        cfg["estimated_seats"] = 2
    cfg["chaise"] = bool(raw.get("chaise", False))
    cfg["legs_visible"] = bool(raw.get("legs_visible", True))
    if cfg["chaise_side"] not in ("left", "right", None):
        cfg["chaise_side"] = None
    return cfg


def analyze_photo(image_bytes: bytes, media_type: str) -> dict:
    """Возвращает конфигурацию дивана. Поле _source = 'ai' | 'manual'."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY не задан — возвращаем ручной режим")
        return {**DEFAULT_CONFIG, "_source": "manual",
                "_note": "Ключ API не настроен — проверьте тип вручную."}

    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        message = client.messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": SYSTEM_PROMPT},
                    ],
                }
            ],
        )
        text = "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )
        cfg = _sanitize(_extract_json(text))
        cfg["_source"] = "ai"
        logger.info("Claude Vision определил тип: %s", cfg.get("type"))
        return cfg
    except Exception as exc:  # noqa: BLE001 — любой сбой -> ручной режим
        logger.exception("Сбой анализа фото: %s", exc)
        return {**DEFAULT_CONFIG, "_source": "manual",
                "_note": f"Не удалось распознать ({exc}). Заполните вручную."}
