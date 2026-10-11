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
from app.models import ACTIVE_STATUSES, OPEN_ORDER_STATUSES, Booking, Branch, Order, Salon, Staff
from app.services import booking as svc
from app.services.availability import branch_tz, now_utc
from app.services import orders as osvc
from app.staffui import MANAGERS, booking_buttons, order_buttons, staff_keyboard
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


async def open_orders_list(session, salon: Salon) -> str:
    rows = (await session.scalars(select(Order).where(Order.salon_id == salon.id, Order.status.in_(OPEN_ORDER_STATUSES))
                                  .order_by(Order.number))).unique().all()
    if not rows:
        return "📦 Ochiq buyurtma yo'q."
    lines = [f"📦 <b>Ochiq buyurtmalar</b> · {len(rows)} ta", ""]
    for o in rows:
        what = ", ".join(f"{_h(i.name)}×{i.qty}" for i in o.items)
        where = "🚚" if o.fulfillment == "delivery" else "🏪"
        lines.append(f"<b>#{o.number}</b> {osvc.LABEL[o.status]} · {where} {_h(o.user.name or 'Mijoz')} · {what} · {o.total:,} so'm".replace(",", " "))
    return "\n".join(LRM + line if line else line for line in lines)


async def on_text(session, salon: Salon, token: str, chat_id: int, text: str) -> bool:
    """A staff chat typed a staff command; False means it was not one (the client flow handles it)."""
    word = text.strip().lower()
    offset = DAY_WORDS.get(word)
    orders = word in ("📦 buyurtmalar", "/buyurtmalar", "buyurtmalar")
    if offset is None and not orders:
        return False
    staff = await staff_of_chat(session, salon, chat_id)
    if staff is None:
        return False
    if orders:
        text_out = await open_orders_list(session, salon) if staff.role in MANAGERS else "Buyurtmalarni rahbar va administrator ko'radi."
    else:
        text_out = await day_list(session, salon, staff, offset)
    await send_menu(salon, token, chat_id, staff, text_out)
    return True


ORDER_MOVES = {"oa": "accepted", "or": "ready", "ov": "delivering", "od": "done", "oy": "cancelled"}


async def on_order_button(session, salon: Salon, token: str, cq: dict, parts: list[str]) -> str:
    """oa/or/ov/od: next step; ox: ask to cancel, oy: yes, on: keep."""
    from app.notifications import notify_staff_order, send_order_update

    chat_id = cq.get("from", {}).get("id")
    staff = await staff_of_chat(session, salon, chat_id)
    order = await session.get(Order, int(parts[1])) if len(parts) == 2 and parts[1].isdigit() else None
    if staff is None or order is None or order.salon_id != salon.id or staff.role not in MANAGERS:
        return "Bu buyurtma topilmadi"
    if parts[0] == "ox":
        await _edit_buttons(token, cq, [[{"text": "Ha, bekor qilish", "callback_data": f"oy:{order.id}"},
                                         {"text": "Yo'q", "callback_data": f"on:{order.id}"}]])
        return "Bekor qilinsinmi? Mijozga xabar boradi."
    if parts[0] == "on":
        await _edit_buttons(token, cq, order_buttons(salon, staff, order.id, order.status, order.fulfillment))
        return "Buyurtma o'z holicha qoldi"
    status = ORDER_MOVES.get(parts[0])
    if status is None:
        return "Bajarildi"
    if not subscription_active(salon):
        return "Obuna muddati tugagan"
    order_id, actor = order.id, staff.id
    try:
        order = await osvc.move(session, order, status)
    except osvc.OrderError as e:
        await _edit_buttons(token, cq, order_buttons(salon, staff, order_id, order.status, order.fulfillment))
        return str(e)[:190]
    await _edit_buttons(token, cq, order_buttons(salon, staff, order_id, order.status, order.fulfillment))
    await send_order_update(order_id)
    await notify_staff_order(order_id, order.status, actor)
    return f"#{order.number}: {osvc.LABEL[order.status]}"


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
