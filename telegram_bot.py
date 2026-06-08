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
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        if not resp.ok:
            logger.warning("Telegram recusou mensagem: %s", resp.text)
    except Exception as e:
        logger.warning("Falha no alerta Telegram: %s", e)


def send_alert_check(text: str) -> dict:
    """Versão do send_alert que retorna a resposta real do Telegram (para diagnóstico)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return {"ok": False, "error": "TELEGRAM_TOKEN ou TELEGRAM_CHAT não configurados"}
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}
