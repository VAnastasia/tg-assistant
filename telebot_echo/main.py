"""
Simple echo bot using pyTelegramBotAPI (telebot).

Token is read from environment variable TELEBOT_TOKEN (from .env if present).
"""
import os
import logging
from pathlib import Path

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
log = logging.getLogger("telebot-echo")

TOKEN = os.getenv("TELEBOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set TELEBOT_TOKEN in .env or environment.")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")


@bot.message_handler(func=lambda message: True)
def echo_all(message):
    """Echo any incoming message."""
    log.info("Echoing from %s: %s", message.from_user.id, message.text)
    bot.reply_to(message, message.text or "")


def main() -> None:
    log.info("Starting echo bot polling...")
    bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)


if __name__ == "__main__":
    main()

