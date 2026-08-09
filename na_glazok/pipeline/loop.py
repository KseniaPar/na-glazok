"""Execution Loop: generate → validate → security review → commit."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from na_glazok.llm.client import chat
from na_glazok.pipeline.heuristics import CALORIE_RE, heuristic_security_override
from na_glazok.prompts.generator import LOOP_GENERATOR_SYSTEM, wrap_user_input
from na_glazok.prompts.security import SECURITY_SYSTEM_PROMPT

log = logging.getLogger("execution_loop")

MAX_ATTEMPTS = 3


@dataclass
class LoopEvent:
    phase: str
    detail: str
    attempt: int = 0


@dataclass
class LoopResult:
    ok: bool
    report: str
    severity: str | None = None
    blocked_by_gateway: bool = False
    attempts: int = 0
    events: list[LoopEvent] = field(default_factory=list)
    error: str | None = None


def has_calorie_numbers(text: str) -> bool:
    if not text or not text.strip():
        return False
    lower = text.lower()
    refuse_markers = (
        "не могу посчитать",
        "не могу рассчитать",
        "не буду считать",
        "нельзя считать",
        "несъедоб",
        "отказ",
        "не является едой",
        "не является пищей",
        "опасно для здоровья",
        "не рекомендую считать",
        "вежливый отказ",
        "пластик",
        "бензин",
        "гвозд",
    )
    if any(m in lower for m in refuse_markers):
        return True
    if not CALORIE_RE.search(text):
        return False
    return bool(re.search(r"\d", text))


def parse_security_verdict(raw: str) -> tuple[str, str, str]:
    text = (raw or "").strip()
    severity = "CLEAN"
    reason = ""
    feedback = "NONE"

    m = re.search(
        r"(?im)^\s*SEVERITY:\s*(CRITICAL|HIGH|MEDIUM|LOW|CLEAN)\s*$",
        text,
    )
    if m:
        severity = m.group(1).upper()
    else:
        for lvl in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "CLEAN"):
            if f"[{lvl}]" in text.upper() or lvl in text.upper():
                severity = lvl
                break

    m = re.search(r"(?im)^\s*REASON:\s*(.+)$", text)
    if m:
        reason = m.group(1).strip()
    m = re.search(r"(?im)^\s*FEEDBACK:\s*(.+)$", text)
    if m:
        feedback = m.group(1).strip()

    return severity, reason, feedback


def _build_generate_messages(
    user_text: str,
    *,
    history: list[dict[str, str]] | None,
    message_stamp: str | None,
    feedback: str | None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": LOOP_GENERATOR_SYSTEM}
    ]

    for turn in history or []:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            if role == "user" and "[USER_INPUT]" not in content:
                content = wrap_user_input(content)
            messages.append({"role": role, "content": content})

    body = user_text.strip()
    if message_stamp:
        body = f"[{message_stamp}] {body}"
    body = wrap_user_input(body)

    messages.append({"role": "user", "content": body})

    if feedback:
        messages.append(
            {
                "role": "user",
                "content": (
                    f"[ИСПРАВЛЕНИЕ ОТ СИСТЕМЫ БЕЗОПАСНОСТИ]\n{feedback}\n"
                    "Перепиши ответ с учётом этого требования."
                ),
            }
        )
    return messages


def _security_messages(user_text: str, draft: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SECURITY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"ЗАПРОС ПОЛЬЗОВАТЕЛЯ:\n{user_text.strip()}\n\n"
                f"ЧЕРНОВИК ОТЧЁТА:\n{draft.strip()}\n\n"
                "Выдай вердикт в требуемом формате."
            ),
        },
    ]


def run_execution_loop(
    user_text: str,
    *,
    history: list[dict[str, str]] | None = None,
    message_stamp: str | None = None,
    user_id: str | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    model: str | None = None,
) -> LoopResult:
    result = LoopResult(ok=False, report="")
    feedback: str | None = None
    uid = user_id or "loop"

    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        result.events.append(
            LoopEvent("attempt_start", f"итерация {attempt}/{max_attempts}", attempt)
        )

        log.info("[LOOP] Фаза 1: генерация (attempt=%s)", attempt)
        try:
            messages = _build_generate_messages(
                user_text,
                history=history,
                message_stamp=message_stamp,
                feedback=feedback,
            )
            draft, _lat, _raw = chat(
                messages,
                model=model,
                temperature=0.3,
                max_tokens=900,
                user_id=f"{uid}:gen:{attempt}",
            )
            draft = (draft or "").strip()
        except Exception as exc:
            detail = str(exc)
            if "400" in detail and (
                "Security Violation" in detail or "secret" in detail.lower()
            ):
                log.error("[LOOP] INPUT BLOCK: секрет/паттерн — цикл прерван")
                result.blocked_by_gateway = True
                result.error = detail
                result.events.append(LoopEvent("gateway_block", detail[:500], attempt))
                result.report = (
                    "Запрос заблокирован защитным шлюзом (секрет/ключ в тексте).\n"
                    "Убери API-ключи из сообщения и попробуй снова."
                )
                return result
            log.exception("[LOOP] Ошибка генерации")
            result.error = detail
            result.events.append(LoopEvent("generate_error", detail[:500], attempt))
            feedback = "Предыдущий ответ сорвался. Сформируй корректный отчёт по еде."
            continue

        result.events.append(
            LoopEvent("generate_ok", draft[:300].replace("\n", " "), attempt)
        )
        log.info("[LOOP] Фаза 1 OK, draft_len=%s", len(draft))

        log.info("[LOOP] Фаза 2: функциональная валидация")
        if not has_calorie_numbers(draft):
            log.warning("[LOOP] Фаза 2 FAIL: нет калорий/ккал")
            result.events.append(
                LoopEvent("validate_fail", "нет калорий/ккал", attempt)
            )
            feedback = "Ты забыл посчитать калории, исправь"
            continue
        result.events.append(LoopEvent("validate_ok", "есть ккал или отказ", attempt))

        log.info("[LOOP] Фаза 3: Security Step")
        try:
            sec_raw, _lat2, _raw2 = chat(
                _security_messages(user_text, draft),
                model=model,
                temperature=0.0,
                max_tokens=400,
                user_id=f"{uid}:sec:{attempt}",
            )
        except Exception as exc:
            detail = str(exc)
            log.exception("[LOOP] Security Step failed")
            result.events.append(LoopEvent("security_error", detail[:500], attempt))
            feedback = (
                "Security Step недоступен. Перепиши ответ максимально безопасно: "
                "без расчёта ядов/несъедобного, без PII."
            )
            continue

        severity, reason, sec_feedback = parse_security_verdict(sec_raw)
        override = heuristic_security_override(user_text, draft, severity)
        if override:
            severity, reason, sec_feedback = override
            log.warning(
                "[LOOP] Security heuristic override → %s (%s)", severity, reason
            )
            result.events.append(
                LoopEvent("security_override", f"{severity}: {reason}", attempt)
            )
        result.severity = severity
        result.events.append(
            LoopEvent(
                "security_verdict",
                f"{severity}: {reason} | feedback={sec_feedback}",
                attempt,
            )
        )
        log.info("[LOOP] Security verdict=%s reason=%s", severity, reason)

        if severity in ("CRITICAL", "HIGH"):
            log.warning("[LOOP] Фаза 4: %s — откат. %s", severity, reason)
            result.events.append(
                LoopEvent("rewind", f"{severity} → regenerate", attempt)
            )
            feedback = (
                sec_feedback
                if sec_feedback.upper() != "NONE"
                else f"Исправь: обнаружен {severity}. {reason}"
            )
            if severity == "CRITICAL":
                feedback = (
                    f"{feedback}\n"
                    "ЗАПРЕЩЕНО считать КБЖУ для пластика, гвоздей, бензина, химии. "
                    "Напиши развёрнутый вежливый ОТКАЗ без цифр калорий для этих веществ."
                )
            if attempt >= max_attempts:
                safe = (
                    "Я не могу посчитать КБЖУ для несъедобных или опасных веществ "
                    "(пластик, гвозди, бензин и т.п.) — это опасно для здоровья. "
                    "Опиши обычную еду, и я помогу с калориями."
                )
                result.events.append(
                    LoopEvent("commit_forced_refusal", f"{severity}: {reason}", attempt)
                )
                result.ok = True
                result.report = safe
                result.severity = severity
                return result
            continue
        if severity in ("MEDIUM", "LOW"):
            log.warning(
                "[LOOP][WARNING] Security %s — commit. %s", severity, reason
            )
            result.events.append(
                LoopEvent("commit_warning", f"{severity}: {reason}", attempt)
            )
            result.ok = True
            result.report = draft
            return result

        result.events.append(LoopEvent("commit_clean", "CLEAN", attempt))
        result.ok = True
        result.report = draft
        return result

    result.events.append(LoopEvent("exhausted", f"max_attempts={max_attempts}"))
    result.ok = False
    result.report = (
        result.report
        or "Не удалось сформировать безопасный отчёт за отведённые попытки. "
        "Попробуй переформулировать запрос."
    )
    result.error = result.error or "max_attempts_exceeded"
    return result


def loop_result_to_dict(res: LoopResult) -> dict[str, Any]:
    return {
        "ok": res.ok,
        "report": res.report,
        "severity": res.severity,
        "blocked_by_gateway": res.blocked_by_gateway,
        "attempts": res.attempts,
        "error": res.error,
        "events": [
            {"phase": e.phase, "detail": e.detail, "attempt": e.attempt}
            for e in res.events
        ],
    }
