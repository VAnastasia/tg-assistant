"""
SQLite helpers for storing Telegram messages.

Uses aiosqlite for non-blocking access from async code.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, List

import aiosqlite


@dataclass(slots=True)
class MessageRecord:
    id: int
    chat_id: int
    sender: str
    text: str
    date: str
    processed: bool = False


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
                    date TEXT NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0
                )
                """
            )

            # Migration: add processed column if missing (legacy DB).
            async with db.execute("PRAGMA table_info(messages)") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]
            if "processed" not in columns:
                await db.execute(
                    """
                    ALTER TABLE messages
                    ADD COLUMN processed INTEGER NOT NULL DEFAULT 0
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
                    INSERT OR IGNORE INTO messages (id, chat_id, sender, text, date, processed)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.chat_id,
                        record.sender,
                        record.text,
                        record.date,
                        int(record.processed),
                    ),
                )
                await db.commit()

    async def count_messages(self) -> int:
        """Convenience helper for debug/testing."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM messages") as cursor:
                row = await cursor.fetchone()
                return int(row[0]) if row else 0

    async def fetch_unprocessed(self, limit: int = 200) -> List[MessageRecord]:
        """Return a batch of unprocessed messages ordered by date."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                """
                SELECT id, chat_id, sender, text, date, processed
                FROM messages
                WHERE processed = 0
                ORDER BY date ASC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    MessageRecord(
                        id=row[0],
                        chat_id=row[1],
                        sender=row[2],
                        text=row[3] or "",
                        date=row[4],
                        processed=bool(row[5]),
                    )
                    for row in rows
                ]

    async def mark_processed(self, ids: Sequence[int]) -> None:
        """Mark given message ids as processed."""
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    f"UPDATE messages SET processed = 1 WHERE id IN ({placeholders})",
                    tuple(ids),
                )
                await db.commit()


# Convenience factory to reuse across modules without circular imports.
def create_db(db_path: str) -> Database:
    return Database(db_path)

