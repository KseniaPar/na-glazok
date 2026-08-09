"""In-process LLM client (guards + upstream, no HTTP loopback)."""
from __future__ import annotations

import os
import time
from typing import Any

import requests

from na_glazok.config import load_openrouter_api_key
from na_glazok.llm.service import RateLimited, SecurityViolation, process_chat_completion
from na_glazok.llm.upstream import DEFAULT_MODEL, OPENROUTER_URL


def _use_direct() -> bool:
    return os.environ.get("LLM_DIRECT", "").strip().lower() in ("1", "true", "yes")


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 512,
    timeout: int = 120,
    retries: int = 3,
    user_id: str | None = None,
) -> tuple[str, float, dict[str, Any]]:
    """
    Returns (content, latency_sec, raw_payload).

    Default: in-process gateway (input/output guards).
    LLM_DIRECT=1: raw OpenRouter (unsafe, debug only).
    """
    model_name = model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL
    body = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    t0 = time.perf_counter()

    if _use_direct():
        key = api_key or load_openrouter_api_key()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://github.com/local/ai-lab",
            "X-Title": "na-glazok-direct",
            "User-Agent": "na-glazok/1.0",
        }
        last_err: Exception | None = None
        payload: dict[str, Any] = {}
        for attempt in range(1, retries + 1):
            try:
                resp = requests.post(
                    OPENROUTER_URL, json=body, headers=headers, timeout=timeout
                )
                payload = resp.json()
                if resp.status_code >= 400:
                    raise RuntimeError(f"LLM HTTP {resp.status_code}: {payload}")
                last_err = None
                break
            except Exception as exc:
                last_err = exc
                if attempt < retries:
                    time.sleep(1.5 * attempt)
                    continue
                raise RuntimeError(f"LLM unavailable after {retries} tries: {exc}") from exc
        if last_err is not None:
            raise RuntimeError(f"LLM unavailable: {last_err}") from last_err
        choices = payload.get("choices") or []
        content = ""
        if choices:
            content = ((choices[0].get("message") or {}).get("content")) or ""
        return content.strip(), time.perf_counter() - t0, payload

    client_id = f"user:{user_id}" if user_id else "local:pipeline"
    try:
        outcome = process_chat_completion(body, client_id=client_id)
    except SecurityViolation as exc:
        raise RuntimeError(f"LLM HTTP {exc.status_code}: {exc}") from exc
    except RateLimited as exc:
        raise RuntimeError(f"LLM HTTP {exc.status_code}: {exc}") from exc

    if not outcome.ok:
        raise RuntimeError(f"LLM HTTP {outcome.status_code}: {outcome.error or outcome.payload}")

    return outcome.content.strip(), time.perf_counter() - t0, outcome.payload
