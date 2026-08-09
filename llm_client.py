"""LLM client for «На Глазок» — по умолчанию через локальный LLM Gateway."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Локальный защитный шлюз (day13). Прямой OpenRouter — только если LLM_DIRECT=1.
DEFAULT_GATEWAY = "http://127.0.0.1:8000/v1"
DIRECT_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-4o-mini"


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def load_api_key() -> str:
    from config import load_openrouter_api_key

    return load_openrouter_api_key()


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
    Send chat completions. Returns (content, latency_sec, raw_payload).

    По умолчанию бьёт в LLM Gateway (Input/Output Guard, cost, rate limit).
    Gateway сам ходит в OpenRouter с настоящим ключом.
    """
    model_name = model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL
    body = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(body).encode("utf-8")

    if _use_direct():
        key = api_key or load_api_key()
        url = f"{DIRECT_BASE.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://github.com/local/ai-lab",
            "X-Title": "na-glazok-direct",
            "User-Agent": "na-glazok/1.0",
        }
    else:
        base = (os.environ.get("LLM_GATEWAY_URL") or DEFAULT_GATEWAY).rstrip("/")
        url = f"{base}/chat/completions"
        # Ключ на gateway не проверяется; реальный ключ живёт у шлюза.
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer local-bot",
            "User-Agent": "na-glazok-bot/1.0",
        }
        if user_id:
            headers["X-User-Id"] = str(user_id)

    t0 = time.perf_counter()
    last_err: Exception | None = None
    payload: dict[str, Any] = {}
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            last_err = None
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            # Gateway security blocks → surface clearly to the bot
            raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
                continue
            hint = ""
            if not _use_direct():
                hint = " Запусти шлюз: python na-glazok/gateway.py"
            raise RuntimeError(
                f"LLM unavailable after {retries} tries: {exc}.{hint}"
            ) from exc
    if last_err is not None:
        raise RuntimeError(f"LLM unavailable: {last_err}") from last_err

    dt = time.perf_counter() - t0
    choices = payload.get("choices") or []
    content = ""
    if choices:
        content = ((choices[0].get("message") or {}).get("content")) or ""
    return content.strip(), dt, payload
