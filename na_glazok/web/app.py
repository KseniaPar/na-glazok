"""aiohttp web UI — login + chat over the same execution loop."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aiohttp import web

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

log = logging.getLogger("na-glazok-web")

STATIC_DIR = Path(__file__).resolve().parent / "static"
COOKIE_NAME = "ngl_session"
SESSION_TTL_SEC = 7 * 24 * 3600


@dataclass
class Session:
    token: str
    chat_id: int
    expires_at: float


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


def _secure_eq(a: str, b: str) -> bool:
    """Length-safe constant-time compare."""
    da = hashlib.sha256(a.encode("utf-8")).digest()
    db = hashlib.sha256(b.encode("utf-8")).digest()
    return hmac.compare_digest(da, db)


def _require_web_creds() -> tuple[str, str]:
    load_dotenv()
    user = (os.environ.get("WEB_USERNAME") or "").strip()
    password = (os.environ.get("WEB_PASSWORD") or "").strip()
    if not user or not password:
        raise RuntimeError(
            "Нет WEB_USERNAME / WEB_PASSWORD. Добавь их в .env (см. .env.example)."
        )
    return user, password


def _chat_id_from_token(token: str) -> int:
    return uuid.UUID(token).int % (10**15)


def create_app() -> web.Application:
    expected_user, expected_password = _require_web_creds()
    sessions: dict[str, Session] = {}

    app = web.Application()
    app["sessions"] = sessions
    app["expected_user"] = expected_user
    app["expected_password"] = expected_password

    def get_session(request: web.Request) -> Session | None:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        sess = sessions.get(token)
        if sess is None:
            return None
        if sess.expires_at < time.time():
            sessions.pop(token, None)
            return None
        return sess

    def set_session_cookie(response: web.Response, token: str) -> None:
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_TTL_SEC,
            httponly=True,
            samesite="Lax",
            path="/",
        )

    def clear_session_cookie(response: web.Response) -> None:
        response.del_cookie(COOKIE_NAME, path="/")

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"ok": True, "service": "na-glazok"})

    async def login_page(request: web.Request) -> web.StreamResponse:
        if get_session(request):
            raise web.HTTPFound("/")
        return web.FileResponse(STATIC_DIR / "login.html")

    async def index(request: web.Request) -> web.StreamResponse:
        if not get_session(request):
            raise web.HTTPFound("/login")
        return web.FileResponse(STATIC_DIR / "index.html")

    async def api_welcome(request: web.Request) -> web.Response:
        if not get_session(request):
            raise web.HTTPUnauthorized(text="login required")
        return web.json_response({"welcome": WELCOME})

    async def api_login(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="invalid json")
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        if not (
            _secure_eq(username, expected_user)
            and _secure_eq(password, expected_password)
        ):
            raise web.HTTPUnauthorized(text="неверный логин или пароль")

        token = str(uuid.uuid4())
        sessions[token] = Session(
            token=token,
            chat_id=_chat_id_from_token(token),
            expires_at=time.time() + SESSION_TTL_SEC,
        )
        resp = web.json_response({"ok": True})
        set_session_cookie(resp, token)
        return resp

    async def api_logout(request: web.Request) -> web.Response:
        token = request.cookies.get(COOKIE_NAME)
        if token:
            sessions.pop(token, None)
        resp = web.json_response({"ok": True})
        clear_session_cookie(resp)
        return resp

    async def api_chat(request: web.Request) -> web.Response:
        sess = get_session(request)
        if not sess:
            raise web.HTTPUnauthorized(text="login required")
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="invalid json")
        text = str(body.get("text") or "").strip()
        if not text:
            raise web.HTTPBadRequest(text="пустой текст")

        msg_time = to_local(datetime.now().astimezone())
        stamp = format_stamp(msg_time)
        prior = for_llm(load_history(sess.chat_id))

        try:
            loop_res = await asyncio.to_thread(
                run_execution_loop,
                text,
                history=prior,
                message_stamp=stamp,
                user_id=str(sess.chat_id),
            )
            reply = strip_md_fences(loop_res.report)
            add_message(sess.chat_id, "user", text, msg_time)
            add_message(sess.chat_id, "assistant", reply, msg_time)
            return web.json_response(
                {
                    "reply": reply,
                    "blocked": bool(loop_res.blocked_by_gateway),
                }
            )
        except Exception as exc:
            log.exception("chat error")
            detail = str(exc)
            if "429" in detail:
                user_msg = "Слишком много запросов. Подожди минуту и попробуй снова."
            else:
                user_msg = (
                    "Не смог посчитать (сеть/ключ). Проверь OPENROUTER_API_KEY.\n"
                    f"Детали: {type(exc).__name__}"
                )
            return web.json_response(
                {"reply": user_msg, "blocked": False, "error": True}
            )

    async def api_reset(request: web.Request) -> web.Response:
        sess = get_session(request)
        if not sess:
            raise web.HTTPUnauthorized(text="login required")
        clear_chat(sess.chat_id)
        return web.json_response({"ok": True})

    app.router.add_get("/health", health)
    app.router.add_get("/login", login_page)
    app.router.add_get("/", index)
    app.router.add_post("/api/login", api_login)
    app.router.add_post("/api/logout", api_logout)
    app.router.add_get("/api/welcome", api_welcome)
    app.router.add_post("/api/chat", api_chat)
    app.router.add_post("/api/reset", api_reset)
    app.router.add_static("/static/", STATIC_DIR, name="static")
    return app


def run() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    load_dotenv()
    host = (os.environ.get("WEB_HOST") or "127.0.0.1").strip()
    port = int((os.environ.get("WEB_PORT") or "8080").strip())
    app = create_app()
    log.info("На Глазок web UI on http://%s:%s", host, port)
    web.run_app(app, host=host, port=port, print=None)


if __name__ == "__main__":
    run()
