"""SMS sending: `console` logs messages (development), `eskiz` sends via Eskiz.uz."""

import logging

import httpx

from app.config import settings

log = logging.getLogger("salon.sms")


class SmsError(Exception):
    pass


class ConsoleSms:
    sent: list[tuple[str, str]] = []  # inspected by tests

    async def send(self, phone: str, text: str) -> None:
        self.sent.append((phone, text))
        log.warning("SMS -> %s: %s", phone, text)


class EskizSms:
    base_url = "https://notify.eskiz.uz/api"

    def __init__(self) -> None:
        self._token: str | None = None

    async def _login(self, client: httpx.AsyncClient) -> str:
        resp = await client.post(
            f"{self.base_url}/auth/login",
            data={"email": settings.eskiz_email, "password": settings.eskiz_password},
        )
        if resp.status_code != 200:
            raise SmsError(f"Eskiz login failed: {resp.status_code} {resp.text[:200]}")
        self._token = resp.json()["data"]["token"]
        return self._token

    async def send(self, phone: str, text: str) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            token = self._token or await self._login(client)
            for attempt in range(2):
                resp = await client.post(
                    f"{self.base_url}/message/sms/send",
                    headers={"Authorization": f"Bearer {token}"},
                    data={"mobile_phone": phone.lstrip("+"), "message": text, "from": settings.eskiz_from},
                )
                if resp.status_code == 401 and attempt == 0:
                    token = await self._login(client)  # token expired
                    continue
                if resp.status_code >= 400:
                    raise SmsError(f"Eskiz send failed: {resp.status_code} {resp.text[:200]}")
                return


class NoSms:
    async def send(self, phone: str, text: str) -> None:
        raise SmsError("SMS o'chirilgan")


_sender = EskizSms() if settings.sms_backend == "eskiz" else NoSms() if settings.sms_backend == "none" else ConsoleSms()


def get_sms():
    return _sender


async def send_safely(phone: str, text: str) -> bool:
    try:
        await get_sms().send(phone, text)
        return True
    except Exception:
        log.exception("SMS to %s failed", phone)
        return False
