"""Local config helpers for «На Глазок»."""
from __future__ import annotations

import os
import re
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (KEY=VALUE). Does not override existing env."""
    env_path = path or (PROJECT_ROOT / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = val


def load_openrouter_api_key() -> str:
    load_dotenv()
    for env_name in ("OPENROUTER_API_KEY", "REAL_OPENAI_API_KEY"):
        env = (os.environ.get(env_name) or "").strip()
        if env:
            return env

    sibling = (
        PROJECT_ROOT.parent
        / "ai-lab"
        / "backend"
        / "src"
        / "main"
        / "resources"
        / "application-local.yml"
    )
    if sibling.is_file():
        text = sibling.read_text(encoding="utf-8")
        m = re.search(r"api-key:\s*(\S+)", text)
        if m:
            key = m.group(1).strip().strip("'\"")
            if key and "PASTE" not in key and key != "sk-not-set":
                return key

    raise RuntimeError(
        "OpenRouter API key not found. Copy .env.example → .env and set "
        "OPENROUTER_API_KEY, or export it in the shell."
    )
