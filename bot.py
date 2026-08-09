"""Telegram-бот «На Глазок» — КБЖУ по ленивому описанию."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from calorie_core import WELCOME
from execution_loop import run_execution_loop
from memory import (
    add_message,
    clear_chat,
    for_llm,
    format_stamp,
    load_history,
    to_local,
)

DIR = Path(__file__).resolve().parent
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("calorie-bot")

CHAT_MODE: dict[int, str] = {}


def load_token() -> str:
    env = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if env:
        return env
    for name in ("telegram-local.txt", ".env"):
        path = DIR / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if name == ".env":
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip().strip("'\"")
        elif text and not text.startswith("#"):
            return text.splitlines()[0].strip().lstrip("\ufeff")
    raise RuntimeError(
        "Нет TELEGRAM_BOT_TOKEN. Задай env или na-glazok/telegram-local.txt"
    )


def strip_md_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


async def cmd_start(message: Message) -> None:
    await message.answer(WELCOME)


async def cmd_reset(message: Message) -> None:
    clear_chat(message.chat.id)
    await message.answer("Долгосрочная память очищена. Пиши еду — считаю с нуля.")


async def cmd_help(message: Message) -> None:
    await message.answer(
        WELCOME
        + "\n\nКоманды:\n/reset — очистить память (до 7 дней в SQLite)\n"
        "/mode baseline|hardened — промпт"
    )


async def cmd_mode(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) >= 2 and parts[1].lower() in ("baseline", "hardened"):
        CHAT_MODE[message.chat.id] = parts[1].lower()
        await message.answer(f"Режим промпта: {CHAT_MODE[message.chat.id]}")
        return
    await message.answer("Использование: /mode baseline  или  /mode hardened")


async def safe_edit(status: Message, text: str, *, html: bool = False) -> None:
    try:
        if html:
            await status.edit_text(text, parse_mode=ParseMode.HTML)
        else:
            await status.edit_text(text, parse_mode=None)
    except Exception:
        try:
            await status.edit_text(text, parse_mode=None)
        except Exception:
            await status.answer(text, parse_mode=None)


async def on_food(message: Message, bot: Bot) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Напиши текстом, что съел 🙂", parse_mode=None)
        return

    chat_id = message.chat.id
    mode = CHAT_MODE.get(chat_id, "hardened")
    msg_time = to_local(message.date)
    stamp = format_stamp(msg_time)
    prior = for_llm(load_history(chat_id))

    await bot.send_chat_action(chat_id, ChatAction.TYPING)
    status = await message.answer("Секунду, считаю калории…", parse_mode=None)

    try:
        loop_res = await asyncio.to_thread(
            run_execution_loop,
            text,
            mode=mode,
            history=prior,
            message_stamp=stamp,
            user_id=str(chat_id),
        )
        reply = strip_md_fences(loop_res.report)
        add_message(chat_id, "user", text, msg_time)
        add_message(chat_id, "assistant", reply, msg_time)
        await safe_edit(status, reply, html=not loop_res.blocked_by_gateway)
    except Exception as exc:
        log.exception("LLM error")
        detail = str(exc)
        if "429" in detail:
            user_msg = "Слишком много запросов. Подожди минуту и попробуй снова."
        else:
            user_msg = (
                "Не смог посчитать (шлюз/сеть). Убедись, что запущен "
                "`python na-glazok/gateway.py`.\n"
                f"Детали: {type(exc).__name__}"
            )
        await safe_edit(status, user_msg, html=False)


async def main() -> None:
    token = load_token()
    bot = Bot(token=token)
    dp = Dispatcher()
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_reset, Command("reset"))
    dp.message.register(cmd_mode, Command("mode"))
    dp.message.register(on_food, F.text)

    me = None
    for attempt in range(1, 6):
        try:
            me = await bot.get_me()
            break
        except Exception as exc:
            log.warning("get_me failed (%s/5): %s", attempt, exc)
            await asyncio.sleep(2 * attempt)
    if me is None:
        raise RuntimeError("Не удалось подключиться к api.telegram.org")

    log.info(
        "Bot @%s started via gateway %s (execution loop + security step)",
        me.username,
        os.environ.get("LLM_GATEWAY_URL") or "http://127.0.0.1:8000/v1",
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped")
        sys.exit(0)
