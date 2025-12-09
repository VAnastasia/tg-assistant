"""
Telegram assistant using Telethon with SQLite persistence.

Features:
- Connect via Telethon client with reconnects.
- List dialogs (chats).
- Fetch recent messages from a chosen chat.
- Live listener that saves new messages to SQLite and logs them.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from telethon import TelegramClient, events
from telethon.errors import RPCError
from telethon.tl.custom.dialog import Dialog
from telethon.tl.custom.message import Message

import config
from db import MessageRecord, create_db


logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("tg-assistant")


async def list_dialogs(client: TelegramClient) -> list[Dialog]:
    """Return available dialogs."""
    dialogs = []
    async for dialog in client.iter_dialogs():
        dialogs.append(dialog)
        log.info("Dialog: %s (id=%s)", dialog.name, dialog.id)
    return dialogs


async def fetch_last_messages(
    client: TelegramClient, chat_id: int, limit: int = 10
) -> list[Message]:
    """Fetch last N messages from a chat."""
    messages = []
    async for message in client.iter_messages(chat_id, limit=limit):
        messages.append(message)
    messages.reverse()  # chronological order
    log.info("Fetched %s messages from chat %s", len(messages), chat_id)
    return messages


async def save_message(db, message: Message, dialog_title: Optional[str] = None) -> None:
    """Persist message to SQLite."""
    record = MessageRecord(
        id=message.id,
        chat_id=message.chat_id,
        sender=message.sender_id or 0,
        text=message.message or "",
        date=message.date.isoformat(),
    )
    await db.save_message(record)
    short_title = dialog_title or str(message.chat_id)
    log.info("[%s] %s: %s", short_title, record.sender, record.text[:80])


async def main() -> None:
    if not config.api_id or not config.api_hash:
        raise RuntimeError("Set api_id and api_hash in config.py or environment.")

    db = create_db(config.db_path)
    await db.init()

    # Configure client with retry options.
    client = TelegramClient(
        session=config.session_name,
        api_id=config.api_id,
        api_hash=config.api_hash,
        connection_retries=5,
        retry_delay=2,
    )

    @client.on(events.NewMessage)
    async def handler(event):
        """Handle new incoming messages in real time."""
        chat = await event.get_chat()
        title = getattr(chat, "title", None) or getattr(chat, "username", None) or str(
            event.chat_id
        )
        await save_message(db, event.message, dialog_title=title)
        print(f"[{title}] {event.message.sender_id}: {event.message.message}")

    try:
        log.info("Connecting to Telegram...")
        await client.start(phone=os.getenv("TG_PHONE"))
        log.info("Connected.")

        dialogs = await list_dialogs(client)
        if not dialogs:
            log.warning("No dialogs available.")
            return

        # Example: take the first dialog for demo purposes.
        selected = dialogs[0]
        log.info("Using dialog: %s (id=%s)", selected.name, selected.id)

        messages = await fetch_last_messages(client, selected.id, limit=100)
        for msg in messages:
            await save_message(db, msg, dialog_title=selected.name)

        log.info("Start listening for new messages...")
        await client.run_until_disconnected()
    except RPCError as exc:
        log.error("Telegram RPC error: %s", exc)
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
    finally:
        await client.disconnect()
        log.info("Client disconnected.")


if __name__ == "__main__":
    asyncio.run(main())

