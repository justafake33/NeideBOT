import os
import logging
import requests

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT", "")


def send_alert(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        logger.debug("Telegram não configurado, ignorando alerta")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
    except Exception as e:
        logger.warning("Falha no alerta Telegram: %s", e)
