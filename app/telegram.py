"""Each salon's own Telegram bot: login by phone contact, booking messages, reminders, review requests.

A salon's bot token is stored encrypted on that salon's row; the key lives only in the server's
environment. Every bot posts to its own webhook address, checked with that salon's secret, so one
salon's clients never reach another salon's bot.
"""

import base64
import logging
import secrets

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings

log = logging.getLogger("salon.telegram")


class TelegramError(Exception):
    pass


# ---------- token at rest ----------

def _fernet() -> Fernet:
    secret = (settings.token_key or settings.jwt_secret).encode()
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"salon-bot-token", info=b"v1").derive(secret)
    return Fernet(base64.urlsafe_b64encode(key))


def seal(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def unseal(sealed: str | None) -> str | None:
    if not sealed:
        return None
    try:
        return _fernet().decrypt(sealed.encode()).decode()
    except InvalidToken:
        log.error("bot token cannot be decrypted: TOKEN_KEY or JWT_SECRET changed")
        return None


def new_secret() -> str:
    return secrets.token_urlsafe(32)


# ---------- Bot API ----------

class TelegramApi:
    base = "https://api.telegram.org"

    async def call(self, token: str, method: str, **params) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{self.base}/bot{token}/{method}", json=params)
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        except (httpx.HTTPError, ValueError) as e:
            # never let the URL (it holds the token) reach a log or a client
            raise TelegramError(f"Telegram bilan aloqa yo'q ({method}: {type(e).__name__})") from None
        if not body.get("ok"):
            raise TelegramError(body.get("description") or f"Telegram {method}: {resp.status_code}")
        return body.get("result")


class FakeTelegram:
    """Used in development and tests: records calls instead of talking to Telegram."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def call(self, token: str, method: str, **params):
        self.calls.append((token, method, params))
        log.warning("TG %s %s", method, params.get("text", "")[:120])
        if method == "getMe":
            if not token.count(":") == 1:
                raise TelegramError("Unauthorized")
            return {"id": int(token.split(":")[0]), "is_bot": True, "username": "demo_salon_bot"}
        return True

    def sent(self, method: str = "sendMessage") -> list[dict]:
        return [p for _, m, p in self.calls if m == method]


_api = TelegramApi() if settings.telegram_backend == "live" else FakeTelegram()


def get_tg():
    return _api


async def send(token: str | None, chat_id: int | None, text: str, buttons: list[list[dict]] | None = None) -> bool:
    """Send a message; never raises, a failed message must not break a booking."""
    if not token or not chat_id:
        return False
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": buttons}
    try:
        await get_tg().call(token, "sendMessage", **params)
        return True
    except Exception:
        log.exception("telegram message to %s failed", chat_id)
        return False
