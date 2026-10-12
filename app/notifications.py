"""Messages to clients and the reminder worker.

Messages go through the salon's own Telegram bot to clients who logged in with it.
SMS is used only where it is switched on (SMS_BACKEND=eskiz) and the client has no Telegram link.
"""

import html
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app import i18n, telegram
from app.config import settings
from app.i18n import t
from app.db import SessionLocal
from app.models import Booking, Order, Reminder, Salon, Staff
from app.services.availability import branch_tz, now_utc
from app.sms import send_safely
from app.tenancy import salon_today, subscription_active
from app.kinds import kind_of
from app.staffui import booking_buttons, order_buttons
from app.text import LRM, iso, phone_display

log = logging.getLogger("salon.notifications")


def _when(booking: Booking) -> str:
    return booking.starts_at.astimezone(branch_tz(booking.branch)).strftime("%d.%m %H:%M")


def _h(s: str) -> str:
    """Typed text in a message: HTML-safe and isolated, so a right-to-left name cannot flip the line."""
    return html.escape(iso(s))


def _som(n: int, lang: str = "uz") -> str:
    return i18n.som(n, lang)


def _lang(b: Booking | Order) -> str:
    return i18n.pick(b.user.language)


def confirmation_text(b: Booking, term: str = "Usta", lang: str = "uz") -> str:
    return t(lang, "b.confirmed", when=_when(b), branch=_h(b.branch.name), term=i18n.term(term, lang), master=_h(b.master.name),
             address=_h(b.branch.address), total=_som(b.total_price, lang))


def reminder_text(b: Booking, kind: str, term: str = "Usta", lang: str = "uz") -> str:
    return t(lang, "b.remind.24h" if kind == "24h" else "b.remind.2h", when=_when(b), term=i18n.term(term, lang),
             master=_h(b.master.name), branch=_h(b.branch.name))


def cancellation_text(b: Booking, lang: str = "uz") -> str:
    return t(lang, "b.cancelled", when=_when(b), branch=_h(b.branch.name))


def review_text(b: Booking, lang: str = "uz") -> str:
    return t(lang, "b.review", master=_h(b.master.name))


def cancel_button(b: Booking, lang: str = "uz") -> list[dict]:
    return [{"text": t(lang, "btn.cancel"), "callback_data": f"cancel:{b.id}"}]


def review_buttons(b: Booking) -> list[list[dict]]:
    return [[{"text": f"{n} ⭐", "callback_data": f"rate:{b.id}:{n}"} for n in range(1, 6)]]


async def deliver(salon: Salon, b: Booking, text: str, buttons: list[list[dict]] | None = None) -> bool:
    """Telegram first; SMS only where it is switched on and the client never linked Telegram.
    False only when a send was attempted and failed (worth retrying); no channel at all counts as done."""
    token = telegram.unseal(salon.tg_token_sealed)
    if token and b.user.tg_chat_id:
        return await telegram.send(token, b.user.tg_chat_id, text, buttons)
    if settings.sms_backend != "none" and b.user.phone.startswith("+998"):  # SMS goes to Uzbek numbers only
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
            lang = _lang(b)
            await deliver(salon, b, confirmation_text(b, kind_of(salon.kind).term, lang), [cancel_button(b, lang)])


async def send_cancelled(booking_id: int) -> None:
    async with SessionLocal() as session:
        b, salon = await _load(session, booking_id)
        if b:
            await deliver(salon, b, cancellation_text(b, _lang(b)))


async def send_moved(booking_id: int) -> None:
    async with SessionLocal() as session:
        b, salon = await _load(session, booking_id)
        if b:
            lang = _lang(b)
            text = t(lang, "b.moved", when=_when(b), branch=_h(b.branch.name), term=i18n.term(kind_of(salon.kind).term, lang),
                     master=_h(b.master.name))
            await deliver(salon, b, text, [cancel_button(b, lang)])


# ---------- the salon's own people: new, cancelled and moved bookings ----------

WEEKDAYS = i18n.WEEKDAYS["uz"]


_phone = phone_display


def staff_text(b: Booking, event: str, term: str = "Usta", lang: str = "uz") -> str:
    local = b.starts_at.astimezone(branch_tz(b.branch))
    client = b.user.name or t(lang, "client")
    if b.user.phone == "walk-in" and b.comment:
        client = b.comment.split(". ")[0]
    lines = [t(lang, "s." + event), f"👤 {_h(client)}"]
    if _phone(b.user.phone):  # its own line: next to a right-to-left name a number would read backwards
        lines.append(f"📞 {_phone(b.user.phone)}")
    lines += [f"✨ {', '.join(_h(i.service_name) for i in b.items)}",
              f"👩‍🎨 {i18n.term(term, lang)}: {_h(b.master.name)}",
              f"🕒 {local:%d.%m}, {i18n.WEEKDAYS[lang][local.weekday()]} {local:%H:%M} · {_h(b.branch.name)}",
              f"💰 {_som(b.total_price, lang)}"]
    if b.comment and b.user.phone != "walk-in":
        lines.append(f"💬 {_h(b.comment)}")
    # Telegram aligns each line by its first letter; a mark in front keeps every line left-aligned,
    # even "👤 س" whose only letter is Arabic.
    return "\n".join(LRM + line for line in lines)


async def notify_staff(booking_id: int, event: str, exclude_staff_id: int | None = None) -> int:
    """Tell the owner, administrators and the booked master (whoever linked their Telegram) what happened.
    The person who made the change is not told about their own action. Returns how many messages went out."""
    async with SessionLocal() as session:
        b, salon = await _load(session, booking_id)
        token = telegram.unseal(salon.tg_token_sealed) if b else None
        if not b or not token:
            return 0
        people = (await session.scalars(
            select(Staff).where(Staff.salon_id == b.salon_id, Staff.is_active, Staff.tg_chat_id.is_not(None))
        )).all()
        rank = {"owner": 0, "admin": 1, "barber": 2}
        chats: dict[int, Staff] = {}  # one message per chat; a person with two accounts gets the higher role's buttons
        for p in sorted(people, key=lambda p: rank.get(p.role, 3)):
            if p.id != exclude_staff_id and (p.role in ("owner", "admin") or p.master_id == b.master_id):
                chats.setdefault(p.tg_chat_id, p)
        sent, term = 0, kind_of(salon.kind).term
        for chat_id, person in sorted(chats.items()):
            text = staff_text(b, event, term, i18n.pick(person.language))
            sent += await telegram.send(token, chat_id, text, booking_buttons(salon, person, b.id, event))
        return sent


async def send_review_request(booking_id: int) -> None:
    """After a visit: ask for stars right in the chat (Telegram only; no SMS for this)."""
    async with SessionLocal() as session:
        b, salon = await _load(session, booking_id)
        token = telegram.unseal(salon.tg_token_sealed) if b else None
        if b and token and b.user.tg_chat_id and b.review is None:
            await telegram.send(token, b.user.tg_chat_id, review_text(b, _lang(b)), review_buttons(b))


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
            lang = _lang(b)
            buttons = [[{"text": t(lang, "btn.come"), "callback_data": f"come:{b.id}"}, *cancel_button(b, lang)]]
            if await deliver(salon, b, reminder_text(b, kind, kind_of(salon.kind).term, lang), buttons):
                sent += 1
    if claimed:
        log.info("reminders: %d claimed, %d sent", len(claimed), sent)
    return sent


# ---------- orders ----------

def order_lines(o: Order, lang: str = "uz") -> list[str]:
    lines = [f"• {_h(i.name)} × {i.qty} = {_som(i.price * i.qty, lang)}" for i in o.items]
    if o.delivery_fee:
        lines.append(f"• {t(lang, 'o.fee')} = {_som(o.delivery_fee, lang)}")
    lines.append(t(lang, "o.total", total=_som(o.total, lang)))
    lines.append(t(lang, "o.to", address=_h(o.address)) if o.fulfillment == "delivery" else t(lang, "o.pickup"))
    return lines


def order_staff_text(o: Order, event: str, lang: str = "uz") -> str:
    lines = [t(lang, "so." + event, n=o.number), f"👤 {_h(o.user.name or t(lang, 'client'))}"]
    if _phone(o.user.phone):
        lines.append(f"📞 {_phone(o.user.phone)}")
    lines += order_lines(o, lang)
    if o.comment:
        lines.append(f"💬 {_h(o.comment)}")
    return "\n".join(LRM + line for line in lines)


async def _load_order(session, order_id: int) -> tuple[Order | None, Salon | None]:
    o = await session.scalar(select(Order).where(Order.id == order_id))
    salon = await session.get(Salon, o.salon_id) if o is not None else None
    if salon is None or not subscription_active(salon):
        return None, None
    return o, salon


async def send_order_update(order_id: int) -> None:
    """The client hears about every step of their order in the business's bot."""
    async with SessionLocal() as session:
        o, salon = await _load_order(session, order_id)
        token = telegram.unseal(salon.tg_token_sealed) if o else None
        if not o or not token or not o.user.tg_chat_id:
            return
        lang = _lang(o)
        ready = t(lang, "o.ready.delivery" if o.fulfillment == "delivery" else "o.ready.pickup")
        text = t(lang, "o." + o.status, n=o.number, ready=ready)
        if o.status == "new":
            text += "\n\n" + "\n".join(order_lines(o, lang))
        await telegram.send(token, o.user.tg_chat_id, "\n".join(LRM + x for x in text.split("\n")))


async def notify_staff_order(order_id: int, event: str, exclude_staff_id: int | None = None) -> int:
    """Owner and administrators (whoever linked Telegram) get the order with the next-step buttons."""
    async with SessionLocal() as session:
        o, salon = await _load_order(session, order_id)
        token = telegram.unseal(salon.tg_token_sealed) if o else None
        if not o or not token:
            return 0
        people = (await session.scalars(select(Staff).where(
            Staff.salon_id == o.salon_id, Staff.is_active, Staff.tg_chat_id.is_not(None), Staff.role.in_(("owner", "admin"))
        ))).all()
        chats: dict[int, Staff] = {}
        for p in sorted(people, key=lambda p: 0 if p.role == "owner" else 1):
            if p.id != exclude_staff_id:
                chats.setdefault(p.tg_chat_id, p)
        sent = 0
        for chat_id, person in sorted(chats.items()):
            text = order_staff_text(o, event, i18n.pick(person.language))
            sent += await telegram.send(token, chat_id, text, order_buttons(salon, person, o.id, o.status, o.fulfillment))
        return sent
