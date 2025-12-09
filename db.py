"""
SQLite helpers for storing Telegram messages.

Uses aiosqlite for non-blocking access from async code.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiosqlite


@dataclass(slots=True)
class MessageRecord:
    id: int
    chat_id: int
    sender: str
    text: str
    date: str


class Database:
    """Async SQLite wrapper for messages."""

    def __init__(self, db_path: str):
        self._db_path = Path(db_path)
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """Initialize database schema."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    sender TEXT NOT NULL,
                    text TEXT,
                    date TEXT NOT NULL
                )
                """
            )
            await db.commit()

    async def save_message(self, record: MessageRecord) -> None:
        """
        Store a message if it is not already present.

        Duplicate check is performed by primary key on `id`.
        """
        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO messages (id, chat_id, sender, text, date)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (record.id, record.chat_id, record.sender, record.text, record.date),
                )
                await db.commit()

    async def count_messages(self) -> int:
        """Convenience helper for debug/testing."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM messages") as cursor:
                row = await cursor.fetchone()
                return int(row[0]) if row else 0


# Convenience factory to reuse across modules without circular imports.
def create_db(db_path: str) -> Database:
    return Database(db_path)

