"""Telegram: the owner connects the salon's bot; clients log in through it; the bot's webhook."""

import hmac
import html
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth, telegram
from app.config import settings
from app.db import get_session
from app.models import Booking, Salon, Staff, TgLogin, User
from app.notifications import _when
from app.ratelimit import Window, client_ip
from app.schemas import UserOut
from app.security import issue_token
from app.services import booking as svc
from app.services import profiles
from app.services.availability import now_utc
from app.tenancy import active_salon, get_salon

router = APIRouter(tags=["telegram"])
LOGIN_TTL = timedelta(minutes=5)
# Keyed by address and salon: many phones share one carrier address in Uzbekistan.
LOGIN_PER_IP = Window(60, 3600)
TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{30,50}$")


# ---------- owner: connect the salon's bot ----------

class BotConnect(BaseModel):
    token: str = Field(max_length=80)


class BotState(BaseModel):
    connected: bool
    username: str = ""
    webhook_url: str = ""


def _owner(staff: Staff) -> None:
    if staff.role != "owner":
        raise HTTPException(403, "Botni faqat salon egasi ulaydi")


@router.get("/s/{slug}/staff/telegram", response_model=BotState)
async def bot_state(staff: Staff = Depends(auth.manager), salon: Salon = Depends(get_salon)):
    return BotState(connected=bool(salon.tg_token_sealed), username=salon.tg_bot_username)


@router.put("/s/{slug}/staff/telegram", response_model=BotState)
async def bot_connect(
    body: BotConnect,
    staff: Staff = Depends(auth.manager),
    salon: Salon = Depends(active_salon),
    session: AsyncSession = Depends(get_session),
):
    _owner(staff)
    token = body.token.strip()
    if not TOKEN_RE.match(token):
        raise HTTPException(422, "Token noto'g'ri ko'rinishda. @BotFather bergan tokenni to'liq nusxalang.")
    if not settings.public_url and settings.telegram_backend == "live":
        raise HTTPException(503, "Server manzili (PUBLIC_URL) sozlanmagan")
    try:
        me = await telegram.get_tg().call(token, "getMe")
    except telegram.TelegramError:
        raise HTTPException(422, "Telegram bu tokenni tanimadi. @BotFather'dan qayta nusxalang.")
    taken = await session.scalar(select(Salon.id).where(Salon.tg_bot_username == me["username"], Salon.id != salon.id))
    if taken:
        raise HTTPException(409, "Bu bot boshqa salonga ulangan. Har bir salon o'z botini ochadi.")
    secret = telegram.new_secret()
    url = f"{settings.public_url.rstrip('/')}/api/tg/{salon.id}"
    try:
        await telegram.get_tg().call(token, "setWebhook", url=url, secret_token=secret,
                                     allowed_updates=["message", "callback_query"], drop_pending_updates=True)
    except telegram.TelegramError as e:
        raise HTTPException(502, f"Telegram webhookni qabul qilmadi: {e}")
    salon.tg_token_sealed = telegram.seal(token)
    salon.tg_bot_username = me["username"]
    salon.tg_webhook_secret = secret
    await session.commit()
    return BotState(connected=True, username=salon.tg_bot_username, webhook_url=url)


@router.delete("/s/{slug}/staff/telegram", status_code=204)
async def bot_disconnect(
    staff: Staff = Depends(auth.manager), salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)
):
    _owner(staff)
    token = telegram.unseal(salon.tg_token_sealed)
    if token:
        try:
            await telegram.get_tg().call(token, "deleteWebhook")
        except telegram.TelegramError:
            pass  # the token may already be revoked; forget it anyway
    salon.tg_token_sealed, salon.tg_bot_username, salon.tg_webhook_secret = None, "", ""
    await session.commit()
    return Response(status_code=204)


# ---------- client: log in on the site through the bot ----------

class LoginStart(BaseModel):
    token: str
    url: str
    bot: str


class LoginState(BaseModel):
    status: str  # pending | done
    user: UserOut | None = None


@router.post("/s/{slug}/auth/telegram", response_model=LoginStart)
async def login_start(request: Request, salon: Salon = Depends(active_salon), session: AsyncSession = Depends(get_session)):
    if not salon.tg_token_sealed:
        raise HTTPException(409, "Salon boti hali ulanmagan. Iltimos, salonga qo'ng'iroq qiling.")
    key = f"{client_ip(request)}|{salon.id}"
    if LOGIN_PER_IP.blocked(key):
        raise HTTPException(429, "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring.")
    LOGIN_PER_IP.hit(key)
    token = telegram.new_secret()[:40]
    session.add(TgLogin(salon_id=salon.id, token=token, expires_at=now_utc() + LOGIN_TTL))
    await session.commit()
    return LoginStart(token=token, url=f"https://t.me/{salon.tg_bot_username}?start={token}", bot=salon.tg_bot_username)


@router.get("/s/{slug}/auth/telegram/{token}", response_model=LoginState)
async def login_poll(token: str, response: Response, salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)):
    """The site asks every couple of seconds; once the client shared their number, the session starts here."""
    login = await session.scalar(
        select(TgLogin).where(TgLogin.token == token, TgLogin.salon_id == salon.id).with_for_update()
    )
    if login is None or login.status == "used" or login.expires_at < now_utc():
        raise HTTPException(410, "Kirish muddati tugadi. Qaytadan bosing.")
    if login.status == "pending":
        return LoginState(status="pending")
    login.status = "used"  # one link, one browser
    user = UserOut.model_validate(await session.get(User, login.user_id))
    await session.commit()
    auth.set_cookie(response, auth.CLIENT_COOKIE, issue_token("client", user.id, salon.id), auth.cookie_path(salon))
    return LoginState(status="done", user=user)


# ---------- webhook: what the salon's bot receives ----------

def _contact_keyboard() -> dict:
    return {"keyboard": [[{"text": "📱 Raqamni yuborish", "request_contact": True}]], "resize_keyboard": True, "one_time_keyboard": True}


async def _reply(token: str, chat_id: int, text: str, markup: dict | None = None) -> None:
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if markup:
        params["reply_markup"] = markup
    try:
        await telegram.get_tg().call(token, "sendMessage", **params)
    except telegram.TelegramError:
        pass


@router.post("/tg/{salon_id}", status_code=200, include_in_schema=False)
async def webhook(
    salon_id: int,
    update_: dict,
    x_telegram_bot_api_secret_token: str = Header(""),
    session: AsyncSession = Depends(get_session),
):
    salon = await session.get(Salon, salon_id)
    # Only this salon's bot knows this salon's secret.
    if salon is None or not salon.tg_webhook_secret or not hmac.compare_digest(x_telegram_bot_api_secret_token, salon.tg_webhook_secret):
        raise HTTPException(403)
    token = telegram.unseal(salon.tg_token_sealed)
    if not token:
        return {}
    if "callback_query" in update_:
        await _on_button(session, salon, token, update_["callback_query"])
    elif "message" in update_:
        await _on_message(session, salon, token, update_["message"])
    return {}


async def _on_message(session: AsyncSession, salon: Salon, token: str, msg: dict) -> None:
    chat_id, text = msg["chat"]["id"], (msg.get("text") or "").strip()
    if msg.get("chat", {}).get("type") != "private":
        return
    now = now_utc()
    if text.startswith("/start"):
        code = text[6:].strip()
        login = await session.scalar(
            select(TgLogin).where(TgLogin.token == code, TgLogin.salon_id == salon.id, TgLogin.status == "pending", TgLogin.expires_at > now)
        ) if code else None
        if login is None:
            await _reply(token, chat_id, f"Assalomu alaykum! Bu <b>{html.escape(salon.name)}</b> boti.\nBron qilish uchun salon saytidan \"Telegram orqali kirish\" tugmasini bosing.")
            return
        login.chat_id = chat_id
        known = await session.scalar(select(User).where(User.salon_id == salon.id, User.tg_chat_id == chat_id))
        if known:  # this chat already shared its number: one tap to confirm, so a link sent by someone else can't log them in
            await session.commit()
            await _reply(token, chat_id, _confirm_text(salon), {"inline_keyboard": [[{"text": "✅ Ha, kirish", "callback_data": f"login:{login.token}"}]]})
            return
        await session.commit()
        await _reply(token, chat_id, f"<b>{html.escape(salon.name)}</b> saytiga kirish uchun telefon raqamingizni yuboring 👇", _contact_keyboard())
        return
    contact = msg.get("contact")
    if contact:
        if contact.get("user_id") != msg.get("from", {}).get("id"):
            await _reply(token, chat_id, "Faqat o'zingizning raqamingizni yuboring: pastdagi tugmani bosing.", _contact_keyboard())
            return
        try:
            phone = auth.normalize_phone(contact.get("phone_number", ""))
        except auth.AuthError:
            await _reply(token, chat_id, "Hozircha faqat O'zbekiston raqamlari (+998) qabul qilinadi.", {"remove_keyboard": True})
            return
        name = " ".join(x for x in (contact.get("first_name"), contact.get("last_name")) if x)[:120]
        user = await session.scalar(select(User).where(User.salon_id == salon.id, User.phone == phone))
        if user is None:
            user = User(salon_id=salon.id, phone=phone, name=name)
            session.add(user)
        elif not user.name:
            user.name = name
        # One chat belongs to one client of this salon.
        await session.execute(update(User).where(User.salon_id == salon.id, User.tg_chat_id == chat_id, User.phone != phone).values(tg_chat_id=None))
        user.tg_chat_id = chat_id
        await session.flush()
        login = await session.scalar(
            select(TgLogin).where(TgLogin.salon_id == salon.id, TgLogin.chat_id == chat_id, TgLogin.status == "pending", TgLogin.expires_at > now_utc())
            .order_by(TgLogin.created_at.desc()).limit(1)
        )
        if login:
            login.user_id, login.status = user.id, "done"
        await session.commit()
        await _reply(token, chat_id, "✅ Rahmat! Saytga qayting, bron shu yerdan davom etadi.\nTasdiq va eslatmalar shu chatga keladi.", {"remove_keyboard": True})
        return
    await _reply(token, chat_id, "Bron qilish uchun salon saytiga kiring. Bronlaringiz haqidagi xabarlar shu yerga keladi.")


def _confirm_text(salon: Salon) -> str:
    return (f"<b>{html.escape(salon.name)}</b> saytiga kirish so'raldi.\n"
            "Agar hozir saytda o'zingiz bosgan bo'lsangiz, tugmani bosing. Aks holda bu xabarga e'tibor bermang.")


async def _confirm_login(session: AsyncSession, salon: Salon, chat_id: int, code: str) -> str:
    login = await session.scalar(
        select(TgLogin).where(TgLogin.token == code, TgLogin.salon_id == salon.id, TgLogin.status == "pending",
                              TgLogin.chat_id == chat_id, TgLogin.expires_at > now_utc()).with_for_update()
    )
    known = await session.scalar(select(User).where(User.salon_id == salon.id, User.tg_chat_id == chat_id))
    if login is None or known is None:
        return "Kirish muddati tugagan. Saytda qaytadan bosing."
    login.user_id, login.status = known.id, "done"
    await session.commit()
    return "✅ Kirdingiz. Saytga qayting."


async def _on_button(session: AsyncSession, salon: Salon, token: str, cq: dict) -> None:
    chat_id = cq.get("from", {}).get("id")
    parts = (cq.get("data") or "").split(":")
    if parts[0] == "login" and len(parts) == 2:
        answer = await _confirm_login(session, salon, chat_id, parts[1])
        if answer.startswith("✅"):
            await _reply(token, chat_id, "✅ Kirdingiz. Saytga qayting, bronni davom ettiring.")
        await _answer(token, cq, answer)
        return
    answer = "Bajarildi"
    booking = None
    if len(parts) >= 2 and parts[1].isdigit():
        booking = await session.get(Booking, int(parts[1]))
    user = await session.get(User, booking.user_id) if booking else None
    # A button works only for this salon's booking of the client who owns this chat.
    if booking is None or booking.salon_id != salon.id or user is None or user.tg_chat_id != chat_id:
        answer = "Bu bron topilmadi"
    elif parts[0] == "cancel":
        when = _when(booking)
        try:
            await svc.cancel_booking(session, booking, by_staff=False)
            answer = "Bron bekor qilindi"
            await _reply(token, chat_id, f"❌ {when} dagi bron bekor qilindi.")
        except svc.BookingError as e:
            answer = str(e)[:190]
    elif parts[0] == "come":
        answer = "Rahmat, sizni kutamiz!"
    elif parts[0] == "rate" and len(parts) == 3 and parts[2] in ("1", "2", "3", "4", "5"):
        try:
            await profiles.create_review(session, user, booking, int(parts[2]), "")
            answer = "Rahmat! Bahoyingiz qabul qilindi."
            await _reply(token, chat_id, f"{'⭐' * int(parts[2])} Rahmat! Fikr yozmoqchi bo'lsangiz, saytdagi \"Bronlarim\" bo'limida yozishingiz mumkin.")
        except profiles.ReviewError as e:
            answer = str(e)[:190]
    await _answer(token, cq, answer)


async def _answer(token: str, cq: dict, text: str) -> None:
    try:
        await telegram.get_tg().call(token, "answerCallbackQuery", callback_query_id=cq.get("id"), text=text)
    except telegram.TelegramError:
        pass
