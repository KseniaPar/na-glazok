"""Deterministic security overrides when LLM inspector is socially engineered."""
from __future__ import annotations

import re

CALORIE_RE = re.compile(r"(?i)(ккал|калори|calories?\b|\d+\s*(ккал|kcal))")

# RU + EN / chemical / packaging — substrings matched on lowercased user text.
_POISON_MARKERS = (
    # Russian
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
    "ядовит",
    "несъедоб",
    # English / IUPAC / packaging (red-team bypasses)
    "plastic",
    "polyethylene",
    "polypropylene",
    "terephthalate",
    "polystyrene",
    "pvc",
    "pet ",
    " nail",
    "nails",
    "steel nail",
    "acetone",
    "gasoline",
    "petrol",
    "diesel",
    "bleach",
    "solvent",
    "antifreeze",
    "cyanide",
    "arsenic",
    "mercury",
    "rat poison",
    "non-edible",
    "inedible",
    "packaging scrap",
    "nail polish remover",
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

_REFUSAL_MARKERS = (
    "не могу",
    "отказ",
    "несъедоб",
    "опасно",
    "не является едой",
    "не является пищей",
    "cannot",
    "can't calculate",
    "not edible",
    "inedible",
    "refuse",
    "unsafe to eat",
)


def heuristic_security_override(
    user_text: str, draft: str, severity: str
) -> tuple[str, str, str] | None:
    u = " " + (user_text or "").lower() + " "
    d = (draft or "").lower()
    rank = {"CLEAN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    cur = rank.get(severity, 0)

    poison_ask = any(m in u for m in _POISON_MARKERS)
    # Also catch brand-style names: Gasoline-Max, Nail-Protein
    if re.search(
        r"(?i)\b(gasoline|nail|acetone|plastic|petrol|diesel|pet)\b",
        user_text or "",
    ) or re.search(
        r"(?i)\b(gasoline|nail|acetone|plastic|petrol|diesel)[-_a-z0-9]*\b",
        user_text or "",
    ):
        poison_ask = True

    has_kcal = bool(CALORIE_RE.search(draft or "")) and bool(
        re.search(r"\d", draft or "")
    )
    refusal = any(x in d for x in _REFUSAL_MARKERS)
    if poison_ask and has_kcal and not refusal and cur < rank["CRITICAL"]:
        return (
            "CRITICAL",
            "Детерминированный override: численный КБЖУ для несъедобного/яда.",
            "ЗАПРЕЩЕНО считать КБЖУ для ядов/несъедобного/химии/гвоздей/пластика "
            "(в т.ч. English/IUPAC и «бренды» вроде Gasoline-Max). "
            "Напиши развёрнутый вежливый отказ БЕЗ цифр калорий.",
        )

    if any(m in d for m in _SECRET_MARKERS) and cur < rank["HIGH"]:
        return (
            "HIGH",
            "Детерминированный override: в черновике похоже на секрет/canary.",
            "Удали из ответа любые ключи, токены и служебные метки. Только КБЖУ по еде.",
        )
    return None
