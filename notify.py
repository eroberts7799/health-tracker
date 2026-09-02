"""Send a message to Ethan's phone via his Telegram bot.

Env: TELEGRAM_BOT_TOKEN (from @BotFather), TELEGRAM_CHAT_ID (Ethan's chat
with the bot — fetch once via the bot's getUpdates after messaging it).
"""

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def send(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    res = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text},
        timeout=30,
    )
    if not res.ok:
        sys.exit(f"telegram send failed: HTTP {res.status_code} — {res.text}")


def send_photo(path: str, caption: str = "") -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    with open(path, "rb") as f:
        res = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "caption": caption},
            files={"photo": f},
            timeout=60,
        )
    if not res.ok:
        sys.exit(f"telegram photo send failed: HTTP {res.status_code} — {res.text}")


def send_document(path: str, caption: str = "") -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    with open(path, "rb") as f:
        res = requests.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "caption": caption},
            files={"document": f},
            timeout=60,
        )
    if not res.ok:
        sys.exit(f"telegram document send failed: HTTP {res.status_code} — {res.text}")


if __name__ == "__main__":
    send(" ".join(sys.argv[1:]) or "health-tracker test ping")
    print("sent")
