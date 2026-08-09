"""SQLite long-term memory for calorie bot (per chat, with timestamps)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from na_glazok.config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "data" / "calories.db"
LOCAL_TZ = timezone(timedelta(hours=3), name="UTC+3")
HISTORY_DAYS = 7


@dataclass
class StoredMessage:
    role: str
    content: str
    created_at: datetime


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_chat_time ON messages(chat_id, created_at)"
    )
    conn.commit()
    return conn


def to_local(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(LOCAL_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def format_stamp(dt: datetime) -> str:
    return to_local(dt).strftime("%Y-%m-%d %H:%M")


def add_message(
    chat_id: int, role: str, content: str, created_at: datetime | None = None
) -> None:
    ts = to_local(created_at).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages(chat_id, role, content, created_at) VALUES (?,?,?,?)",
            (chat_id, role, content, ts),
        )
        conn.commit()


def clear_chat(chat_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        conn.commit()


def load_history(chat_id: int, *, days: int = HISTORY_DAYS) -> list[StoredMessage]:
    since = (datetime.now(LOCAL_TZ) - timedelta(days=days)).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT role, content, created_at FROM messages
            WHERE chat_id = ? AND created_at >= ?
            ORDER BY id ASC
            """,
            (chat_id, since),
        ).fetchall()
    out: list[StoredMessage] = []
    for role, content, created_at in rows:
        dt = datetime.fromisoformat(created_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        out.append(StoredMessage(role=role, content=content, created_at=dt))
    return out


def for_llm(history: list[StoredMessage]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for m in history:
        stamp = format_stamp(m.created_at)
        if m.role == "user":
            body = f"[{stamp}] {m.content}"
        else:
            body = f"[{stamp} ответ бота]\n{m.content}"
        messages.append({"role": m.role, "content": body})
    return messages
