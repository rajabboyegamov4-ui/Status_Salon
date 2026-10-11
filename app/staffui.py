"""What the salon's own people see in its Telegram bot: buttons under booking messages and the staff keyboard.

Kept apart from the handlers (app/staffbot.py) so that the message sender can use it without import cycles.
"""

from app.config import settings
from app.models import Salon, Staff

MANAGERS = ("owner", "admin")


def panel_url(salon: Salon, staff: Staff) -> str:
    """The admin panel for owner and administrators, the master's own panel for a master (https only)."""
    base = settings.public_url.rstrip("/")
    if not base.startswith("https://"):
        return ""
    return f"{base}/{salon.slug}/{'admin' if staff.role in MANAGERS else 'panel'}"


def booking_buttons(salon: Salon, staff: Staff, booking_id: int, event: str) -> list[list[dict]]:
    """Under a booking message: cancel right from Telegram (asks once more), or open the panel inside Telegram."""
    rows: list[list[dict]] = []
    if event in ("new", "moved"):
        rows.append([{"text": "❌ Bekor qilish", "callback_data": f"sx:{booking_id}"}])
    url = panel_url(salon, staff)
    if url:
        rows.append([{"text": "📋 Boshqaruv", "web_app": {"url": url}}])
    return rows


def staff_keyboard(salon: Salon, staff: Staff) -> dict:
    """Always under the message box of a staff chat: today's and tomorrow's list, and the panel."""
    rows: list[list[dict]] = [[{"text": "📋 Bugun"}, {"text": "📋 Ertaga"}]]
    url = panel_url(salon, staff)
    if url:
        rows.append([{"text": "⚙️ Boshqaruv", "web_app": {"url": url}}])
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}
