"""What the salon's own people see in its Telegram bot: buttons under booking messages and the staff keyboard.

Kept apart from the handlers (app/staffbot.py) so that the message sender can use it without import cycles.
"""

from app.config import settings
from app.i18n import t
from app.models import Salon, Staff

MANAGERS = ("owner", "admin")


def _modules(salon: Salon) -> list[str]:
    return (salon.modules or "booking").split(",")


def panel_url(salon: Salon, staff: Staff) -> str:
    """The admin panel for owner and administrators, the master's own panel for a master, the orders desk for a
    business that only takes orders (https only)."""
    base = settings.public_url.rstrip("/")
    if not base.startswith("https://"):
        return ""
    if "booking" not in _modules(salon):
        return f"{base}/{salon.slug}/buyurtmalar"
    return f"{base}/{salon.slug}/{'admin' if staff.role in MANAGERS else 'panel'}"


def orders_url(salon: Salon) -> str:
    base = settings.public_url.rstrip("/")
    return f"{base}/{salon.slug}/buyurtmalar" if base.startswith("https://") else ""


def order_buttons(salon: Salon, staff: Staff, order_id: int, status: str, fulfillment: str) -> list[list[dict]]:
    """Under an order message: the next step in one tap; cancel asks once more."""
    lang = staff.language
    cancel = (t(lang, "btn.cancel"), f"ox:{order_id}")
    given = (t(lang, "sb.given"), f"od:{order_id}")
    step = {
        "new": [(t(lang, "sb.accept"), f"oa:{order_id}"), cancel],
        "accepted": [(t(lang, "sb.ready"), f"or:{order_id}"), cancel],
        "ready": ([(t(lang, "sb.on_way"), f"ov:{order_id}")] if fulfillment == "delivery" else []) + [given],
        "delivering": [given],
    }.get(status, [])
    rows = [[{"text": t, "callback_data": d} for t, d in step]] if step else []
    url = orders_url(salon)
    if url and staff.role in MANAGERS:
        rows.append([{"text": t(staff.language, "sk.orders"), "web_app": {"url": url}}])
    return rows


def booking_buttons(salon: Salon, staff: Staff, booking_id: int, event: str) -> list[list[dict]]:
    """Under a booking message: cancel right from Telegram (asks once more), or open the panel inside Telegram."""
    rows: list[list[dict]] = []
    if event in ("new", "moved"):
        rows.append([{"text": t(staff.language, "btn.cancel"), "callback_data": f"sx:{booking_id}"}])
    url = panel_url(salon, staff)
    if url:
        rows.append([{"text": t(staff.language, "sb.panel"), "web_app": {"url": url}}])
    return rows


def staff_keyboard(salon: Salon, staff: Staff) -> dict:
    """Always under the message box of a staff chat: today's and tomorrow's list, the panel, the other language."""
    lang = staff.language
    rows: list[list[dict]] = [[{"text": t(lang, "sk.today")}, {"text": t(lang, "sk.tomorrow")}]] if "booking" in _modules(salon) else []
    if "orders" in _modules(salon) and staff.role in MANAGERS:
        rows.append([{"text": t(lang, "sk.orders")}])
    url = panel_url(salon, staff)
    if url:
        rows.append([{"text": t(lang, "sk.panel"), "web_app": {"url": url}}])
    rows.append([{"text": t(lang, "sk.lang")}])
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}
