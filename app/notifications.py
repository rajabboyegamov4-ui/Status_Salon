"""Messages to clients and the reminder worker.

Messages go through the salon's own Telegram bot to clients who logged in with it.
SMS is used only where it is switched on (SMS_BACKEND=eskiz) and the client has no Telegram link.
"""

import html
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app import telegram
from app.config import settings
from app.db import SessionLocal
from app.models import Booking, Reminder, Salon
from app.services.availability import branch_tz, now_utc
from app.sms import send_safely
from app.tenancy import salon_today, subscription_active

log = logging.getLogger("salon.notifications")


def _when(booking: Booking) -> str:
    return booking.starts_at.astimezone(branch_tz(booking.branch)).strftime("%d.%m %H:%M")


def _h(s: str) -> str:
    return html.escape(s or "")


def _som(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def confirmation_text(b: Booking) -> str:
    return (f"✅ <b>Bron tasdiqlandi</b>\n{_when(b)} · {_h(b.branch.name)}\nBarber: {_h(b.master.name)}\n"
            f"Manzil: {_h(b.branch.address)}\nJami: {_som(b.total_price)} so'm")


def reminder_text(b: Booking, kind: str) -> str:
    lead = "Ertaga" if kind == "24h" else "2 soatdan keyin"
    return f"⏰ {lead} ({_when(b)}) sizni kutamiz.\nBarber: {_h(b.master.name)} · {_h(b.branch.name)}"


def cancellation_text(b: Booking) -> str:
    return f"❌ {_when(b)} dagi bron bekor qilindi. {_h(b.branch.name)}"


def review_text(b: Booking) -> str:
    return f"Tashrifingiz uchun rahmat! {_h(b.master.name)}ning ishini baholang:"


def cancel_button(b: Booking) -> list[dict]:
    return [{"text": "❌ Bekor qilish", "callback_data": f"cancel:{b.id}"}]


def review_buttons(b: Booking) -> list[list[dict]]:
    return [[{"text": f"{n} ⭐", "callback_data": f"rate:{b.id}:{n}"} for n in range(1, 6)]]


async def deliver(salon: Salon, b: Booking, text: str, buttons: list[list[dict]] | None = None) -> bool:
    """Telegram first; SMS only where it is switched on and the client never linked Telegram.
    False only when a send was attempted and failed (worth retrying); no channel at all counts as done."""
    token = telegram.unseal(salon.tg_token_sealed)
    if token and b.user.tg_chat_id:
        return await telegram.send(token, b.user.tg_chat_id, text, buttons)
    if settings.sms_backend != "none" and b.user.phone.startswith("+"):
        plain = text.replace("<b>", "").replace("</b>", "")
        return await send_safely(b.user.phone, html.unescape(plain))
    return True


async def _load(session, booking_id: int) -> tuple[Booking | None, Salon | None]:
    b = await session.scalar(
        select(Booking)
        .options(joinedload(Booking.user), joinedload(Booking.branch), joinedload(Booking.master))
        .where(Booking.id == booking_id)
    )
    salon = await session.get(Salon, b.salon_id) if b is not None else None
    if salon is None or not subscription_active(salon):
        return None, None  # a salon that stopped paying sends nothing
    return b, salon


async def send_confirmation(booking_id: int) -> None:
    async with SessionLocal() as session:
        b, salon = await _load(session, booking_id)
        if b:
            await deliver(salon, b, confirmation_text(b), [cancel_button(b)])


async def send_cancelled(booking_id: int) -> None:
    async with SessionLocal() as session:
        b, salon = await _load(session, booking_id)
        if b:
            await deliver(salon, b, cancellation_text(b))


async def send_moved(booking_id: int) -> None:
    async with SessionLocal() as session:
        b, salon = await _load(session, booking_id)
        if b:
            text = f"🔁 <b>Bron vaqti o'zgardi</b>\nYangi vaqt: {_when(b)} · {_h(b.branch.name)}\nBarber: {_h(b.master.name)}"
            await deliver(salon, b, text, [cancel_button(b)])


async def send_review_request(booking_id: int) -> None:
    """After a visit: ask for stars right in the chat (Telegram only; no SMS for this)."""
    async with SessionLocal() as session:
        b, salon = await _load(session, booking_id)
        token = telegram.unseal(salon.tg_token_sealed) if b else None
        if b and token and b.user.tg_chat_id and b.review is None:
            await telegram.send(token, b.user.tg_chat_id, review_text(b), review_buttons(b))


async def send_due_reminders(batch: int = 50) -> int:
    """Send reminders that are due. Each one is claimed and committed before it is sent, so a
    restart mid-run never sends it twice and a slow Telegram never holds locks that cancels wait on."""
    now = now_utc()
    async with SessionLocal() as session:
        rows = (
            await session.scalars(
                select(Reminder)
                .join(Booking, Booking.id == Reminder.booking_id)
                .join(Salon, Salon.id == Booking.salon_id)
                .where(
                    Salon.is_active,  # a salon that stopped paying gets no messages
                    Salon.paid_until >= salon_today(),
                    Reminder.sent_at.is_(None),
                    Reminder.send_at <= now,
                    Booking.status == "confirmed",
                    Booking.starts_at > now,
                )
                .order_by(Reminder.send_at)
                .limit(batch)
                .with_for_update(of=Reminder, skip_locked=True)
            )
        ).all()
        claimed = [(r.booking_id, r.kind, r.send_at) for r in rows]
        for r in rows:
            r.sent_at = now
        await session.commit()
    sent = 0
    for booking_id, kind, send_at in claimed:
        if now - send_at > timedelta(hours=1):
            continue  # a reminder more than an hour late is pointless
        async with SessionLocal() as session:
            b, salon = await _load(session, booking_id)
            if b is None or b.status != "confirmed":
                continue
            buttons = [[{"text": "✅ Kelaman", "callback_data": f"come:{b.id}"}, *cancel_button(b)]]
            if await deliver(salon, b, reminder_text(b, kind), buttons):
                sent += 1
    if claimed:
        log.info("reminders: %d claimed, %d sent", len(claimed), sent)
    return sent
