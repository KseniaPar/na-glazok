"""Shared chat-completion pipeline: guards → upstream → output guard (in-process)."""
from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

from na_glazok.config import load_openrouter_api_key
from na_glazok.gateway.audit import (
    PRICE_INPUT_1K,
    PRICE_OUTPUT_1K,
    append_audit,
    check_rate_limit,
    count_tokens,
)
from na_glazok.gateway.guards import (
    apply_output_guard,
    ensure_system_message,
    find_secret_hit,
    message_text,
    redact_pii,
    sanitize_encoded_instructions,
    scrub_secrets,
)
from na_glazok.llm.upstream import DEFAULT_MODEL, call_upstream

logger = logging.getLogger("LLM_Gateway")


@dataclass
class ChatOutcome:
    ok: bool
    status_code: int
    payload: dict[str, Any]
    error: str | None = None
    content: str = ""


class SecurityViolation(RuntimeError):
    """Raised when input guard blocks a secret."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimited(RuntimeError):
    def __init__(self, message: str = "Too Many Requests. Rate limit exceeded.") -> None:
        super().__init__(message)
        self.status_code = 429


def _resolve_api_key() -> str:
    if os.environ.get("GATEWAY_MOCK", "").strip().lower() in ("1", "true", "yes"):
        return os.environ.get("OPENROUTER_API_KEY") or "mock-key"
    return load_openrouter_api_key()


def process_chat_completion(
    body: dict[str, Any],
    *,
    client_id: str = "local",
    skip_rate_limit: bool = False,
) -> ChatOutcome:
    """
    Full gateway path used by HTTP /v1/chat/completions and in-process llm.client.
    Mutates a deep copy of messages (PII redact); caller body is not mutated.
    """
    if not skip_rate_limit and not check_rate_limit(client_id):
        logger.warning(f"Клиент {client_id} превысил лимит запросов!")
        append_audit(
            {
                "ip": client_id,
                "action": "rate_limited",
                "input": None,
                "output": None,
                "in_tokens": 0,
                "out_tokens": 0,
                "cost_usd": 0.0,
            }
        )
        raise RateLimited()

    work = copy.deepcopy(body)
    messages = work.get("messages") or []
    if not isinstance(messages, list):
        return ChatOutcome(
            ok=False,
            status_code=400,
            payload={"error": "messages must be a list"},
            error="messages must be a list",
        )

    # Hard-block только по последнему user-сообщению (не по истории/system).
    # Иначе старые red-team пейлоады с sk-… блокируют «яблоко».
    user_msgs = [m for m in messages if m.get("role") == "user"]
    latest_user = message_text(user_msgs[-1].get("content")) if user_msgs else ""
    full_user_input = " ".join(message_text(m.get("content")) for m in user_msgs)

    secret_hit = find_secret_hit(latest_user)
    if secret_hit:
        action = (
            "blocked_base64" if secret_hit == "BASE64_SECRET" else "blocked_secret"
        )
        logger.error(
            f"ЗАБЛОКИРОВАНО: Обнаружен критический секрет {secret_hit} от IP {client_id}"
        )
        append_audit(
            {
                "ip": client_id,
                "action": action,
                "secret_type": secret_hit,
                "input": latest_user,
                "output": None,
                "in_tokens": 0,
                "out_tokens": 0,
                "cost_usd": 0.0,
            }
        )
        err = (
            "Security Violation: Base64 encoded secret patterns detected!"
            if secret_hit == "BASE64_SECRET"
            else (
                f"Security Violation: Hardcoded secret [{secret_hit}] "
                "detected in prompt!"
            )
        )
        raise SecurityViolation(err)

    # История: вычистить секреты, чтобы не утекали в upstream
    for msg in messages:
        if msg.get("role") in ("user", "assistant"):
            msg["content"] = scrub_secrets(message_text(msg.get("content")))

    # Убрать hex-инъекции и «декодируй/выполни» до модели
    for msg in messages:
        if msg.get("role") == "user":
            msg["content"] = sanitize_encoded_instructions(
                message_text(msg.get("content"))
            )

    for msg in messages:
        msg["content"] = redact_pii(message_text(msg.get("content")))

    redacted_input = " ".join(message_text(m.get("content")) for m in messages)
    work["messages"] = ensure_system_message(messages)
    work["model"] = work.get("model") or DEFAULT_MODEL

    try:
        api_key = _resolve_api_key()
    except RuntimeError as exc:
        return ChatOutcome(
            ok=False,
            status_code=503,
            payload={"error": str(exc)},
            error=str(exc),
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/local/ai-lab-challenge",
        "X-Title": "na-glazok-gateway",
        "User-Agent": "na-glazok-gateway/1.0",
    }

    logger.info(
        f"Пропуск чистого/отмаскированного промпта в OpenAI/OpenRouter для IP {client_id}"
    )
    logger.info(f"Входящий (после redaction): {redacted_input[:500]}")

    try:
        response = call_upstream(work, headers)
    except requests.RequestException as exc:
        logger.error(f"Upstream error: {exc}")
        return ChatOutcome(
            ok=False,
            status_code=502,
            payload={"error": f"Upstream unavailable: {exc}"},
            error=str(exc),
        )

    try:
        response_data = response.json()
    except ValueError:
        return ChatOutcome(
            ok=False,
            status_code=response.status_code,
            payload={"error": response.text[:500]},
            error=response.text[:500],
        )

    if response.status_code != 200:
        return ChatOutcome(
            ok=False,
            status_code=response.status_code,
            payload=response_data if isinstance(response_data, dict) else {"error": response_data},
            error=str(response_data)[:500],
        )

    choices = response_data.get("choices") or []
    if not choices:
        return ChatOutcome(
            ok=False,
            status_code=502,
            payload={"error": "Empty choices from upstream"},
            error="Empty choices from upstream",
        )

    ai_reply = (choices[0].get("message") or {}).get("content") or ""
    ai_reply, block_reason = apply_output_guard(ai_reply)
    response_data["choices"][0]["message"]["content"] = ai_reply
    action = "output_blocked" if block_reason else "proxied"

    in_tokens = count_tokens(redacted_input)
    out_tokens = count_tokens(ai_reply)
    cost = ((in_tokens / 1000) * PRICE_INPUT_1K) + (
        (out_tokens / 1000) * PRICE_OUTPUT_1K
    )
    logger.info(
        f"Запрос успешно завершен. Токены: Вход={in_tokens}, Выход={out_tokens} "
        f"| Стоимость: ${cost:.6f}"
    )
    if block_reason:
        logger.info(f"Output Guard сработал: {block_reason}")

    append_audit(
        {
            "ip": client_id,
            "action": action,
            "block_reason": block_reason,
            "input": full_user_input,
            "redacted_input": redacted_input,
            "output": ai_reply,
            "in_tokens": in_tokens,
            "out_tokens": out_tokens,
            "cost_usd": round(cost, 8),
        }
    )

    return ChatOutcome(
        ok=True,
        status_code=200,
        payload=response_data,
        content=ai_reply,
    )
