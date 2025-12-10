"""
Telegram bot on telebot that proxies user questions to ProxyAPI.

Environment variables (can be placed in .env):
- TELEBOT_TOKEN: Telegram bot token.
- PROXYAPI_TOKEN: API token for ProxyAPI (Bearer token).
- PROXYAPI_BASE_URL: Optional, defaults to ProxyAPI OpenAI-compatible endpoint.
- PROXYAPI_MODEL: Optional, model name (e.g., gpt-4o-mini).
"""
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

import requests
import telebot


def load_env_file(path: str = ".env") -> None:
    """Minimal .env loader (does not override existing env)."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file()

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger("telebot-proxyapi")

TELEBOT_TOKEN = os.getenv("TELEBOT_TOKEN")
PROXYAPI_TOKEN = os.getenv("PROXYAPI_TOKEN")
PROXYAPI_BASE_URL = os.getenv(
    "PROXYAPI_BASE_URL", "https://api.proxyapi.ru/openai/v1/chat/completions"
)
PROXYAPI_MODEL = os.getenv("PROXYAPI_MODEL", "gpt-4o-mini")

if not TELEBOT_TOKEN:
    raise RuntimeError("Set TELEBOT_TOKEN in .env or environment.")

if not PROXYAPI_TOKEN:
    raise RuntimeError("Set PROXYAPI_TOKEN in .env or environment.")


bot = telebot.TeleBot(TELEBOT_TOKEN, parse_mode="HTML")


def call_proxyapi(user_prompt: str, history: List[Dict[str, str]] | None = None) -> str:
    """
    Send a chat completion request to ProxyAPI and return the assistant reply text.
    history: optional previous messages in OpenAI chat format.
    """
    messages: List[Dict[str, str]] = history[:] if history else []
    messages.append({"role": "user", "content": user_prompt})

    payload: Dict[str, Any] = {
        "model": PROXYAPI_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {PROXYAPI_TOKEN}",
        "Content-Type": "application/json",
    }

    log.debug("Sending request to ProxyAPI: %s", json.dumps(payload, ensure_ascii=False))
    response = requests.post(PROXYAPI_BASE_URL, headers=headers, json=payload, timeout=60)
    if not response.ok:
        raise RuntimeError(f"ProxyAPI error {response.status_code}: {response.text}")

    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected ProxyAPI response: {data}") from exc
    return content


@bot.message_handler(commands=["start", "help"])
def on_start(message):
    bot.reply_to(
        message,
        "Привет! Отправь вопрос, я спрошу ProxyAPI и верну ответ.",
    )


@bot.message_handler(content_types=["text"])
def handle_question(message):
    question = message.text or ""
    log.info("Request from %s: %s", message.from_user.id, question)
    try:
        answer = call_proxyapi(question)
        bot.reply_to(message, answer)
    except Exception as exc:  # noqa: BLE001 - user-facing fallback
        log.exception("Failed to get ProxyAPI response")
        bot.reply_to(
            message,
            "Не удалось получить ответ от ProxyAPI. Проверь токен/подключение и попробуй снова.",
        )


def main() -> None:
    log.info("Starting bot polling...")
    bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)


if __name__ == "__main__":
    main()
