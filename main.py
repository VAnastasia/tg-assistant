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
import json
from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Optional, Sequence, List, Dict, Any

import requests
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

PROXYAPI_TOKEN = os.getenv("PROXYAPI_TOKEN")
PROXYAPI_BASE_URL = os.getenv(
    "PROXYAPI_BASE_URL", "https://api.proxyapi.ru/openai/v1/chat/completions"
)
PROXYAPI_MODEL = os.getenv("PROXYAPI_MODEL", "gpt-4o-mini")

if not PROXYAPI_TOKEN:
    log.warning("PROXYAPI_TOKEN is not set; /find command will fail until provided.")


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
        processed=False,
    )
    await db.save_message(record)
    short_title = dialog_title or str(message.chat_id)
    log.info("[%s] %s: %s", short_title, record.sender, record.text[:80])


async def collect_unread_archived_channels(
    client: TelegramClient, db, hours: int = 24
) -> int:
    """
    Fetch unread messages from archived channels for the last `hours`.

    - Filters dialogs in the archive (folder_id=1).
    - Only channels are processed.
    - Messages older than cutoff are skipped.
    - Deduplication is guaranteed by DB primary key + INSERT OR IGNORE.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    collected = 0

    async for dialog in client.iter_dialogs(folder=1):
        if not dialog.is_channel:
            continue

        read_max = getattr(dialog.dialog, "read_inbox_max_id", None) or 0

        async for msg in client.iter_messages(dialog.id, min_id=read_max):
            msg_date = msg.date
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            else:
                msg_date = msg_date.astimezone(timezone.utc)

            if msg_date < cutoff:
                # Messages are returned newest-first; break when older than cutoff.
                break

            await save_message(db, msg, dialog_title=dialog.name)
            collected += 1

    return collected


def _chat_link(chat_id: int, message_id: int) -> str:
    """
    Build a t.me link for a channel message.

    For private/supergroup/channel ids shaped as -100xxxxxxxxxx we strip the -100 prefix.
    """
    raw = abs(chat_id)
    if raw > 10**12:
        raw = raw - 1000000000000
    return f"https://t.me/c/{raw}/{message_id}"


def build_prompt_payload(records: Sequence[MessageRecord]) -> str:
    """
    Prepare textual payload for LLM: numbered messages with text and links.
    """
    lines = []
    for rec in records:
        link = _chat_link(rec.chat_id, rec.id)
        lines.append(f"{rec.id}: {rec.text}\nLink: {link}")
    return "\n\n".join(lines)


def call_proxyapi(prompt: str) -> Dict[str, Any]:
    """Synchronous call to ProxyAPI chat completions."""
    headers = {
        "Authorization": f"Bearer {PROXYAPI_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": PROXYAPI_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты фильтр вакансий. Получаешь список сообщений с id, текстом и ссылкой.\n"
                    "Нужно оставить только вакансии для: фронтенд-разработчик (JS/TS, React, Vue, "
                    "Next, Angular, HTML/CSS) или промпт-инженер / LLM engineer.\n"
                    "Исключи всё остальное: новости, демо, обсуждения, резюме/самопрезентации, "
                    "продажи, другие роли.\n"
                    "Ответ только JSON: {\"matches\":[{\"id\": <int>, \"summary\": \"кратко на русском\"}]}.\n"
                    "Если подходящих нет — верни {\"matches\":[]}.\n"
                    "Не придумывай id, используй только переданные. Никакого лишнего текста."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "stream": False,
    }
    resp = requests.post(PROXYAPI_BASE_URL, headers=headers, json=payload, timeout=60)
    if not resp.ok:
        raise RuntimeError(f"ProxyAPI error {resp.status_code}: {resp.text}")
    return resp.json()


def _parse_proxyapi_json(content: str) -> Dict[str, Any] | None:
    """
    Try to parse ProxyAPI textual content into JSON.

    Handles cases with ```json fences or extra text.
    """
    if not content:
        return None
    text = content.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.strip("`")
        # After stripping backticks, remove leading possible "json\n"
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # Fallback: find first {...} block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return None
    return None


async def find_vacancies(db, client: TelegramClient, reply_fn) -> None:
    """
    Fetch unprocessed messages, send to ProxyAPI, and reply with summary + links.
    reply_fn: callable to send reply text (event.respond or similar).
    """
    records = await db.fetch_unprocessed(limit=200)
    if not records:
        await reply_fn("Новых необработанных сообщений нет.")
        return

    prompt = (
        "Вот список сообщений формата: <id>: <text>\\nLink: <url>.\n"
        "Выбери только вакансии фронтенд-разработчика (JS/TS, React/Vue/Next/Angular, HTML/CSS) "
        "или промпт-инженера/LLM engineer. Игнорируй резюме, новости, митапы, продажи, другие роли.\n"
        "Верни строго JSON: {\"matches\":[{\"id\": <int>, \"summary\": \"кратко на русском\"}]}.\n"
        "Если нет подходящих — верни {\"matches\":[]}.\n\n"
        "Сообщения:\n\n"
        + build_prompt_payload(records)
    )

    try:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, call_proxyapi, prompt)
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = _parse_proxyapi_json(content)
    except Exception as exc:  # noqa: BLE001
        log.exception("ProxyAPI failure")
        await reply_fn("Не удалось получить ответ ProxyAPI.")
        return

    if not parsed:
        await reply_fn("Не удалось разобрать ответ ProxyAPI (не JSON).")
        return

    matches = parsed.get("matches") or []
    if not matches:
        await reply_fn("Подходящих вакансий не найдено.")
        await db.mark_processed([r.id for r in records])
        return

    lines = ["Найдено:"]
    matched_ids = []
    for item in matches:
        msg_id = item.get("id")
        summary = item.get("summary", "")
        if not msg_id:
            continue
        rec = next((r for r in records if r.id == msg_id), None)
        if not rec:
            continue
        link = _chat_link(rec.chat_id, rec.id)
        lines.append(f"- {summary} — {link}")
        matched_ids.append(rec.id)

    if not matched_ids:
        await reply_fn("Подходящих вакансий не найдено.")
    else:
        await reply_fn("\n".join(lines))

    await db.mark_processed([r.id for r in records])


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

    @client.on(events.NewMessage(pattern="/find"))
    async def find_handler(event):
        """Handle /find command to search vacancies in unprocessed messages."""
        await event.respond("Ищу подходящие вакансии...")
        await find_vacancies(db, client, event.respond)

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

        archived_collected = await collect_unread_archived_channels(client, db, hours=24)
        log.info("Collected %s unread archived channel messages (last 24h).", archived_collected)

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

