"""Audit log (JSONL) and token/cost helpers."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tiktoken

from na_glazok.config import PROJECT_ROOT

logger = logging.getLogger("LLM_Gateway")

AUDIT_LOG_PATH = PROJECT_ROOT / "audit.jsonl"

PRICE_INPUT_1K = 0.00015
PRICE_OUTPUT_1K = 0.00060

RATE_LIMIT_STORE: dict[str, list[float]] = {}
LIMIT_REQUESTS = int(os.environ.get("GATEWAY_RATE_LIMIT", "5"))
LIMIT_WINDOW = 60


def reload_rate_limit_from_env() -> None:
    global LIMIT_REQUESTS
    LIMIT_REQUESTS = int(os.environ.get("GATEWAY_RATE_LIMIT", "5"))


def count_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    try:
        encoding = tiktoken.encoding_for_model(model)
        return len(encoding.encode(text))
    except Exception:
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except Exception:
            return max(1, len(text) // 4)


def append_audit(record: dict[str, Any]) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def check_rate_limit(client_id: str) -> bool:
    """Return True if allowed; False if limited. Records timestamp on allow."""
    import time

    reload_rate_limit_from_env()
    current_time = time.time()
    user_requests = RATE_LIMIT_STORE.get(client_id, [])
    user_requests = [t for t in user_requests if current_time - t < LIMIT_WINDOW]
    if len(user_requests) >= LIMIT_REQUESTS:
        RATE_LIMIT_STORE[client_id] = user_requests
        return False
    user_requests.append(current_time)
    RATE_LIMIT_STORE[client_id] = user_requests
    return True
