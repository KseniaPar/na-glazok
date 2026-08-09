"""python -m na_glazok → Telegram-бот."""
from __future__ import annotations

import os


def main() -> None:
    os.environ.setdefault("GATEWAY_RATE_LIMIT", "40")
    from na_glazok.bot.main import run

    run()


if __name__ == "__main__":
    main()
