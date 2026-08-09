"""OpenRouter upstream call (monkeypatchable in tests)."""
from __future__ import annotations

import os
from typing import Any

import requests

OPENROUTER_URL = (
    os.environ.get("OPENROUTER_URL")
    or "https://openrouter.ai/api/v1/chat/completions"
)
DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL") or "openai/gpt-4o-mini"


def call_upstream(body: dict[str, Any], headers: dict[str, str]) -> requests.Response:
    """POST to OpenRouter. Tests monkeypatch this function."""
    return requests.post(OPENROUTER_URL, json=body, headers=headers, timeout=120)
