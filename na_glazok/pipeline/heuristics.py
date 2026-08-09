"""Deterministic security overrides when LLM inspector is socially engineered."""
from __future__ import annotations

import re

CALORIE_RE = re.compile(r"(?i)(ккал|калори|calories?\b|\d+\s*(ккал|kcal))")

_POISON_MARKERS = (
    "пластик",
    "сайдинг",
    "гвозд",
    "бензин",
    "ацетон",
    "растворител",
    "антифриз",
    "крысиный яд",
    "цианид",
    "отбеливател",
    "хлорк",
    "ртут",
    "мышьяк",
)
_SECRET_MARKERS = (
    "sk-",
    "akia",
    "ghp_",
    "sk-or-v1-",
    "xoxb-",
    "bearer ",
    "canary-ngl-",
)


def heuristic_security_override(
    user_text: str, draft: str, severity: str
) -> tuple[str, str, str] | None:
    u = (user_text or "").lower()
    d = (draft or "").lower()
    rank = {"CLEAN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    cur = rank.get(severity, 0)

    poison_ask = any(m in u for m in _POISON_MARKERS)
    has_kcal = bool(CALORIE_RE.search(draft or "")) and bool(
        re.search(r"\d", draft or "")
    )
    refusal = any(
        x in d
        for x in ("не могу", "отказ", "несъедоб", "опасно", "не является едой")
    )
    if poison_ask and has_kcal and not refusal and cur < rank["CRITICAL"]:
        return (
            "CRITICAL",
            "Детерминированный override: численный КБЖУ для несъедобного/яда.",
            "ЗАПРЕЩЕНО считать КБЖУ для ядов/несъедобного. Напиши вежливый отказ без цифр.",
        )

    if any(m in d for m in _SECRET_MARKERS) and cur < rank["HIGH"]:
        return (
            "HIGH",
            "Детерминированный override: в черновике похоже на секрет/canary.",
            "Удали из ответа любые ключи, токены и служебные метки. Только КБЖУ по еде.",
        )
    return None
