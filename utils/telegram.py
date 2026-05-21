"""
Telegram sender — uses MarkdownV2 for clean formatting.

Required env vars:
  TELEGRAM_BOT_TOKEN  — your bot token from BotFather
  TELEGRAM_CHAT_ID    — your personal or group chat ID
"""

import os
import logging
import requests

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


def send_telegram_message(text: str) -> bool:
    """Send a message via your Telegram bot. Returns True on success."""
    token   = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload = {
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "Markdown",   # backtick tables + *bold* + _italic_
        "disable_web_page_preview": True,
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False
