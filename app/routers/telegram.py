"""Telegram: the owner connects the salon's bot; clients log in through it; the bot's webhook."""

import hashlib
import hmac
import html
import json
import re
from datetime import timedelta
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth, staffbot, telegram
from app.config import settings
from app.db import get_session
from app.models import Booking, Salon, Staff, TgLogin, User
from app.notifications import _when, notify_staff
from app.ratelimit import Window, client_ip
from app.schemas import UserOut
from app.security import issue_token
from app.services import booking as svc
from app.services import profiles
from app.services.availability import now_utc
from app.text import clean_name, iso
from app.tenancy import active_salon, get_salon

router = APIRouter(tags=["telegram"])
LOGIN_TTL = timedelta(minutes=5)
# Keyed by address and salon: many phones share one carrier address in Uzbekistan.
LOGIN_PER_IP = Window(60, 3600)
TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{30,50}$")
STAFF_CODE_RE = re.compile(r"^st\d{1,9}_[0-9a-f]{24}$")


# ---------- owner: connect the salon's bot ----------

class BotConnect(BaseModel):
    token: str = Field(max_length=80)


class BotState(BaseModel):
    connected: bool
    username: str = ""
    webhook_url: str = ""
    me_linked: bool = False  # this staff member gets new-booking messages in Telegram
    me_link: str = ""  # open it in Telegram to start getting them


def site_url(salon: Salon) -> str:
    """The booking page; Telegram opens it inside the bot as a Mini App (needs an https address)."""
    base = settings.public_url.rstrip("/")
    return f"{base}/{salon.slug}" if base.startswith("https://") else ""


def book_button(salon: Salon) -> dict | None:
    url = site_url(salon)
    return {"inline_keyboard": [[{"text": "📅 Bron qilish", "web_app": {"url": url}}]]} if url else None


# ---------- staff: get new-booking messages in the salon's bot ----------

def _staff_sig(staff: Staff) -> str:
    msg = f"staff-tg:{staff.salon_id}:{staff.id}:{staff.token_version}".encode()
    return hmac.new(settings.jwt_secret.encode(), msg, hashlib.sha256).hexdigest()[:24]


def staff_start_code(staff: Staff) -> str:
    """Goes into t.me/<bot>?start=...; a new password (token_version) makes old links useless."""
    return f"st{staff.id}_{_staff_sig(staff)}"


def _state(salon: Salon, staff: Staff, webhook_url: str = "") -> BotState:
    connected = bool(salon.tg_token_sealed)
    link = f"https://t.me/{salon.tg_bot_username}?start={staff_start_code(staff)}" if connected else ""
    return BotState(connected=connected, username=salon.tg_bot_username if connected else "", webhook_url=webhook_url,
                    me_linked=connected and staff.tg_chat_id is not None, me_link=link)


def _owner(staff: Staff) -> None:
    if staff.role != "owner":
        raise HTTPException(403, "Botni faqat salon egasi ulaydi")


@router.get("/s/{slug}/staff/telegram", response_model=BotState)
async def bot_state(staff: Staff = Depends(auth.manager), salon: Salon = Depends(get_salon)):
    return _state(salon, staff)


@router.get("/s/{slug}/staff/telegram/me", response_model=BotState)
async def staff_state(staff: Staff = Depends(auth.current_staff), salon: Salon = Depends(get_salon)):
    """Any staff member, a master too: is my Telegram linked, and the link to do it."""
    return _state(salon, staff)


@router.delete("/s/{slug}/staff/telegram/me", response_model=BotState)
async def staff_unlink(
    staff: Staff = Depends(auth.current_staff), salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)
):
    staff.tg_chat_id = None
    await session.commit()
    return _state(salon, staff)


class BotError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


async def attach_bot(session: AsyncSession, salon: Salon, token: str, moving_from: Salon | None = None) -> str:
    """Point a bot at this salon: webhook with a fresh secret, the menu button, the token sealed. No commit.
    `moving_from`: a demo salon giving its bot away (the platform reuses demo bots)."""
    if not TOKEN_RE.match(token):
        raise BotError(422, "Token noto'g'ri ko'rinishda. @BotFather bergan tokenni to'liq nusxalang.")
    if not settings.public_url and settings.telegram_backend == "live":
        raise BotError(503, "Server manzili (PUBLIC_URL) sozlanmagan")
    try:
        me = await telegram.get_tg().call(token, "getMe")
    except telegram.TelegramError:
        raise BotError(422, "Telegram bu tokenni tanimadi. @BotFather'dan qayta nusxalang.")
    others = select(Salon.id).where(Salon.tg_bot_username == me["username"], Salon.id != salon.id)
    if moving_from is not None:
        others = others.where(Salon.id != moving_from.id)
    if await session.scalar(others):
        raise BotError(409, "Bu bot boshqa salonga ulangan. Har bir salon o'z botini ochadi.")
    secret = telegram.new_secret()
    url = f"{settings.public_url.rstrip('/')}/api/tg/{salon.id}"
    try:
        await telegram.get_tg().call(token, "setWebhook", url=url, secret_token=secret,
                                     allowed_updates=["message", "callback_query"], drop_pending_updates=True)
    except telegram.TelegramError as e:
        raise BotError(502, f"Telegram webhookni qabul qilmadi: {e}")
    if site_url(salon):  # the round button next to the message box opens the booking page inside Telegram
        try:
            await telegram.get_tg().call(token, "setChatMenuButton",
                                         menu_button={"type": "web_app", "text": "Bron qilish", "web_app": {"url": site_url(salon)}})
        except telegram.TelegramError:
            pass
    if moving_from is not None:  # the old salon forgets the bot and the staff chats linked through it
        moving_from.tg_token_sealed, moving_from.tg_bot_username, moving_from.tg_webhook_secret = None, "", ""
        await session.execute(update(Staff).where(Staff.salon_id == moving_from.id).values(tg_chat_id=None))
        await session.flush()
    if salon.tg_bot_username != me["username"]:  # another bot: staff link their chats again
        await session.execute(update(Staff).where(Staff.salon_id == salon.id).values(tg_chat_id=None))
    salon.tg_token_sealed = telegram.seal(token)
    salon.tg_bot_username = me["username"]
    salon.tg_webhook_secret = secret
    return url


@router.put("/s/{slug}/staff/telegram", response_model=BotState)
async def bot_connect(
    body: BotConnect,
    staff: Staff = Depends(auth.manager),
    salon: Salon = Depends(active_salon),
    session: AsyncSession = Depends(get_session),
):
    _owner(staff)
    try:
        url = await attach_bot(session, salon, body.token.strip())
    except BotError as e:
        raise HTTPException(e.status, str(e))
    await session.commit()
    await session.refresh(staff)
    return _state(salon, staff, webhook_url=url)


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
    await session.execute(update(Staff).where(Staff.salon_id == salon.id).values(tg_chat_id=None))
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


# ---------- client: the booking page opened inside Telegram (Mini App) ----------

class WebAppLogin(BaseModel):
    init_data: str = Field(max_length=4096)


WEBAPP_MAX_AGE = timedelta(days=1)


def webapp_user_id(init_data: str, bot_token: str) -> int | None:
    """Telegram signs what it hands the Mini App with the bot's token; a forged or stale one gives None."""
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    got = fields.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    if not got or not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), got):
        return None
    try:
        signed_at = int(fields.get("auth_date", "0"))
        user_id = int(json.loads(fields.get("user", "{}")).get("id"))
    except (ValueError, TypeError):
        return None
    if now_utc().timestamp() - signed_at > WEBAPP_MAX_AGE.total_seconds():
        return None
    return user_id


@router.post("/s/{slug}/auth/webapp", response_model=UserOut)
async def webapp_login(
    body: WebAppLogin, response: Response, salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)
):
    """Inside the salon's bot the client is already known by the number they shared: no second login."""
    token = telegram.unseal(salon.tg_token_sealed)
    user_id = webapp_user_id(body.init_data, token) if token else None
    if user_id is None:
        raise HTTPException(401, "Telegram ma'lumoti tasdiqlanmadi")
    user = await session.scalar(select(User).where(User.salon_id == salon.id, User.tg_chat_id == user_id))
    if user is None:
        raise HTTPException(404, "Avval botga telefon raqamingizni yuboring")
    auth.set_cookie(response, auth.CLIENT_COOKIE, issue_token("client", user.id, salon.id), auth.cookie_path(salon))
    return UserOut.model_validate(user)


@router.post("/s/{slug}/staff/webapp")
async def staff_webapp_login(
    body: WebAppLogin, response: Response, salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)
):
    """The panel opened from the bot's "Boshqaruv" button: the linked Telegram account is the login."""
    from app.routers.staff import _me
    token = telegram.unseal(salon.tg_token_sealed)
    user_id = webapp_user_id(body.init_data, token) if token else None
    if user_id is None:
        raise HTTPException(401, "Telegram ma'lumoti tasdiqlanmadi")
    staff = await staffbot.staff_of_chat(session, salon, user_id)
    if staff is None:
        raise HTTPException(404, "Bu Telegram panelga ulanmagan. Avval login va parol bilan kiring.")
    auth.set_cookie(response, auth.STAFF_COOKIE, auth.staff_token(staff), auth.cookie_path(salon))
    return await _me(session, staff, salon)


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
        if STAFF_CODE_RE.match(code):
            await _link_staff(session, salon, token, chat_id, code)
            return
        login = await session.scalar(
            select(TgLogin).where(TgLogin.token == code, TgLogin.salon_id == salon.id, TgLogin.status == "pending", TgLogin.expires_at > now)
        ) if code else None
        if login is None:
            await _greet(session, salon, token, chat_id)
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
    if text and await staffbot.on_text(session, salon, token, chat_id, text):
        return
    contact = msg.get("contact")
    if contact:
        if contact.get("user_id") != msg.get("from", {}).get("id"):
            await _reply(token, chat_id, "Faqat o'zingizning raqamingizni yuboring: pastdagi tugmani bosing.", _contact_keyboard())
            return
        try:
            phone = auth.normalize_phone(contact.get("phone_number", ""), foreign=True)
        except auth.AuthError:
            await _reply(token, chat_id, "Telefon raqamingizni qabul qila olmadik. Iltimos, salonga qo'ng'iroq qiling.", {"remove_keyboard": True})
            return
        name = clean_name(" ".join(x for x in (contact.get("first_name"), contact.get("last_name")) if x))
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
        if login:
            await _reply(token, chat_id, "✅ Rahmat! Saytga qayting, bron shu yerdan davom etadi.\nTasdiq va eslatmalar shu chatga keladi.", {"remove_keyboard": True})
            return
        await _reply(token, chat_id, "✅ Obuna bo'ldingiz! Tasdiq va eslatmalar shu chatga keladi.", {"remove_keyboard": True})
        await _reply(token, chat_id, "Bron qilish uchun pastdagi tugmani bosing 👇", book_button(salon))
        return
    known = await session.scalar(select(User.id).where(User.salon_id == salon.id, User.tg_chat_id == chat_id))
    if known is None:
        await _greet(session, salon, token, chat_id)
        return
    await _reply(token, chat_id, "Bron qilish uchun pastdagi tugmani bosing. Bronlaringiz haqidagi xabarlar shu yerga keladi.", book_button(salon))


async def _greet(session: AsyncSession, salon: Salon, token: str, chat_id: int) -> None:
    """A plain /start: a known client gets the booking button, a new one is asked for their number first."""
    name = html.escape(salon.name)
    staff = await staffbot.staff_of_chat(session, salon, chat_id)
    if staff:
        await staffbot.send_menu(salon, token, chat_id, staff, f"Assalomu alaykum, {html.escape(iso(staff.name))}! <b>{name}</b> boshqaruvi pastdagi tugmalarda.")
        return
    user = await session.scalar(select(User).where(User.salon_id == salon.id, User.tg_chat_id == chat_id))
    if user:
        hello = f", {html.escape(iso(user.name))}" if user.name else ""
        await _reply(token, chat_id, f"Assalomu alaykum{hello}! <b>{name}</b> ga xush kelibsiz.\nBron qilish uchun pastdagi tugmani bosing 👇", book_button(salon))
        return
    await _reply(token, chat_id, f"Assalomu alaykum! Bu <b>{name}</b> boti.\nObuna bo'lish va bron qilish uchun telefon raqamingizni yuboring 👇", _contact_keyboard())


async def _link_staff(session: AsyncSession, salon: Salon, token: str, chat_id: int, code: str) -> None:
    head, _, sig = code[2:].partition("_")
    staff = await session.get(Staff, int(head)) if head.isdigit() else None
    if staff is None or staff.salon_id != salon.id or not staff.is_active or not hmac.compare_digest(sig, _staff_sig(staff)):
        await _reply(token, chat_id, "Havola eskirgan. Admin paneldagi \"Telegram'da xabar olish\" tugmasini qaytadan bosing.")
        return
    staff.tg_chat_id = chat_id
    await session.commit()
    await staffbot.send_menu(salon, token, chat_id, staff,
                             f"✅ {html.escape(iso(staff.name))}, endi <b>{html.escape(salon.name)}</b>dagi yangi, bekor qilingan va ko'chirilgan "
                             "bronlar haqida xabar shu chatga keladi.\n\n📋 <b>Bugun</b> va <b>Ertaga</b> tugmalari kunlik ro'yxatni ko'rsatadi, "
                             "⚙️ <b>Boshqaruv</b> panelni shu yerning o'zida ochadi.")


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
    if parts[0] in ("sx", "sy", "sn"):
        await _answer(token, cq, await staffbot.on_button(session, salon, token, cq, parts))
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
        when, booking_id = _when(booking), booking.id
        try:
            await svc.cancel_booking(session, booking, by_staff=False)
            answer = "Bron bekor qilindi"
            await _reply(token, chat_id, f"❌ {when} dagi bron bekor qilindi.")
            await notify_staff(booking_id, "cancelled")
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
