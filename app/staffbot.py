"""The salon's people working from its Telegram bot, without opening a website.

A staff member whose Telegram is linked (admin panel -> "Telegram'da xabar olish") can:
- see today's or tomorrow's bookings ("📋 Bugun", "📋 Ertaga", or /bugun, /ertaga);
- cancel a booking from the message about it (asked twice, the client is told);
- open the admin panel (owner, administrator) or their own panel (master) inside Telegram.
A master sees and touches only their own bookings, exactly as in the web panel.
"""

import html
from datetime import datetime, timedelta

from sqlalchemy import select

from app import telegram
from app.kinds import kind_of
from app.models import ACTIVE_STATUSES, Booking, Branch, Salon, Staff
from app.services import booking as svc
from app.services.availability import branch_tz, now_utc
from app.staffui import MANAGERS, booking_buttons, staff_keyboard
from app.tenancy import subscription_active
from app.text import LRM, iso, phone_display

DAY_WORDS = {"/bugun": 0, "📋 bugun": 0, "bugun": 0, "/ertaga": 1, "📋 ertaga": 1, "ertaga": 1}
WEEKDAYS = ("dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba")


async def staff_of_chat(session, salon: Salon, chat_id: int) -> Staff | None:
    """The active staff account linked to this chat; with two (an owner who is also a master) the higher role."""
    rows = (await session.scalars(
        select(Staff).where(Staff.salon_id == salon.id, Staff.tg_chat_id == chat_id, Staff.is_active)
    )).all()
    order = {"owner": 0, "admin": 1, "barber": 2}
    return min(rows, key=lambda s: order.get(s.role, 3)) if rows else None


async def send_menu(salon: Salon, token: str, chat_id: int, staff: Staff, text: str) -> None:
    await _send(token, chat_id, text, staff_keyboard(salon, staff))


async def _send(token: str, chat_id: int, text: str, markup: dict | None = None) -> None:
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if markup:
        params["reply_markup"] = markup
    try:
        await telegram.get_tg().call(token, "sendMessage", **params)
    except telegram.TelegramError:
        pass


def _h(s: str | None) -> str:
    return html.escape(iso(s))


async def day_list(session, salon: Salon, staff: Staff, offset: int) -> str:
    branches = (await session.scalars(select(Branch).where(Branch.salon_id == salon.id).order_by(Branch.id))).all()
    if not branches:
        return "Filial yo'q."
    tz = branch_tz(branches[0])
    day = now_utc().astimezone(tz).date() + timedelta(days=offset)
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    stmt = (select(Booking).where(Booking.salon_id == salon.id, Booking.status.in_(ACTIVE_STATUSES + ("completed", "no_show")),
                                  Booking.starts_at >= start, Booking.starts_at < start + timedelta(days=1))
            .order_by(Booking.starts_at, Booking.master_id))
    if staff.role not in MANAGERS:
        stmt = stmt.where(Booking.master_id == staff.master_id)
    rows = (await session.scalars(stmt)).unique().all()
    head = f"📋 <b>{'Bugun' if offset == 0 else 'Ertaga'}, {day:%d.%m}, {WEEKDAYS[day.weekday()]}</b>"
    if not rows:
        return f"{head}\nBron yo'q."
    term = kind_of(salon.kind).term
    total = sum(b.total_price for b in rows if b.status != "no_show")
    lines = [f"{head} · {len(rows)} ta", ""]
    marks = {"completed": "✅ ", "no_show": "🚫 "}
    for b in rows:
        local = b.starts_at.astimezone(tz)
        who = b.user.name or "Mijoz"
        if b.user.phone == "walk-in" and b.comment:
            who = b.comment.split(". ")[0]
        line = f"{marks.get(b.status, '')}<b>{local:%H:%M}</b> {_h(who)} · {', '.join(_h(i.service_name) for i in b.items)}"
        if staff.role in MANAGERS:
            line += f" · {term}: {_h(b.master.name)}"
        if phone_display(b.user.phone):
            line += f"\n      📞 {phone_display(b.user.phone)}"
        lines.append(line)
    lines += ["", f"💰 {total:,} so'm".replace(",", " ")]
    return "\n".join(LRM + line if line else line for line in lines)


async def on_text(session, salon: Salon, token: str, chat_id: int, text: str) -> bool:
    """A staff chat typed a staff command; False means it was not one (the client flow handles it)."""
    offset = DAY_WORDS.get(text.strip().lower())
    if offset is None:
        return False
    staff = await staff_of_chat(session, salon, chat_id)
    if staff is None:
        return False
    await send_menu(salon, token, chat_id, staff, await day_list(session, salon, staff, offset))
    return True


async def _edit_buttons(token: str, cq: dict, rows: list[list[dict]]) -> None:
    m = cq.get("message") or {}
    if not m.get("message_id"):
        return
    try:
        await telegram.get_tg().call(token, "editMessageReplyMarkup", chat_id=m["chat"]["id"], message_id=m["message_id"],
                                     reply_markup={"inline_keyboard": rows})
    except telegram.TelegramError:
        pass


async def on_button(session, salon: Salon, token: str, cq: dict, parts: list[str]) -> str:
    """sx: ask to cancel, sy: yes cancel, sn: keep. Returns the short answer shown at the top of the chat."""
    from app.notifications import notify_staff, send_cancelled  # the sender imports staffui, not this module

    chat_id = cq.get("from", {}).get("id")
    staff = await staff_of_chat(session, salon, chat_id)
    booking = await session.get(Booking, int(parts[1])) if len(parts) == 2 and parts[1].isdigit() else None
    if staff is None or booking is None or booking.salon_id != salon.id:
        return "Bu bron topilmadi"
    if staff.role not in MANAGERS and booking.master_id != staff.master_id:
        return "Bu sizning broningiz emas"
    if parts[0] == "sx":
        if booking.status not in ACTIVE_STATUSES:
            await _edit_buttons(token, cq, booking_buttons(salon, staff, booking.id, "cancelled"))
            return "Bu bron allaqachon faol emas"
        await _edit_buttons(token, cq, [[{"text": "Ha, bekor qilish", "callback_data": f"sy:{booking.id}"},
                                         {"text": "Yo'q", "callback_data": f"sn:{booking.id}"}]])
        return "Bekor qilinsinmi? Mijozga xabar boradi."
    if parts[0] == "sn":
        await _edit_buttons(token, cq, booking_buttons(salon, staff, booking.id, "new"))
        return "Bron o'z holicha qoldi"
    if parts[0] == "sy":
        if not subscription_active(salon):
            return "Obuna muddati tugagan"
        booking_id, actor = booking.id, staff.id
        try:
            await svc.cancel_booking(session, booking, by_staff=True)
        except svc.BookingError as e:
            await _edit_buttons(token, cq, booking_buttons(salon, staff, booking_id, "cancelled"))
            return str(e)[:190]
        await _edit_buttons(token, cq, booking_buttons(salon, staff, booking_id, "cancelled"))
        await _send(token, chat_id, f"{LRM}❌ Bron bekor qilindi. Mijozga xabar yuborildi.")
        await send_cancelled(booking_id)
        await notify_staff(booking_id, "cancelled", actor)
        return "Bekor qilindi"
    return "Bajarildi"
