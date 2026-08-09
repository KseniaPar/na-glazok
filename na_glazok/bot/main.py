"""Telegram bot — thin wrapper around execution loop."""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from na_glazok.config import load_dotenv
from na_glazok.memory import (
    add_message,
    clear_chat,
    for_llm,
    format_stamp,
    load_history,
    to_local,
)
from na_glazok.pipeline.loop import run_execution_loop
from na_glazok.prompts.generator import WELCOME

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("calorie-bot")


def load_token() -> str:
    load_dotenv()
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if token:
        return token
    raise RuntimeError(
        "Нет TELEGRAM_BOT_TOKEN. Добавь его в .env (см. .env.example)."
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
        WELCOME + "\n\nКоманды:\n/reset — очистить память\n/help"
    )


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
    msg_time = to_local(message.date)
    stamp = format_stamp(msg_time)
    prior = for_llm(load_history(chat_id))

    await bot.send_chat_action(chat_id, ChatAction.TYPING)
    status = await message.answer("Секунду, считаю калории…", parse_mode=None)

    try:
        loop_res = await asyncio.to_thread(
            run_execution_loop,
            text,
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
                "Не смог посчитать (сеть/ключ). Проверь OPENROUTER_API_KEY "
                "и что бот запущен: `python -m na_glazok`.\n"
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

    log.info("Bot @%s started (in-process gateway + execution loop)", me.username)
    await dp.start_polling(bot)


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped")
        sys.exit(0)


if __name__ == "__main__":
    run()
