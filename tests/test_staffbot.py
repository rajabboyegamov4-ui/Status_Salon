"""The salon's people working from Telegram: lists, cancelling from the message, the panel without a password."""

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Booking
from app.telegram import get_tg
from tests.conftest import API, local, make_client, staff_login, tomorrow
from tests.test_demo_flow import JASUR_CHAT, OWNER_CHAT, link_staff, sign_init, to
from tests.test_telegram import ME, connect_bot, hook, msg, salon_row


def tap(c, salon, who: int, data: str, message_id: int = 10):
    cq = {"id": "q", "from": {"id": who}, "data": data, "message": {"message_id": message_id, "chat": {"id": who}}}
    return hook(c, salon, {"update_id": 9, "callback_query": cq})


def last(method: str) -> dict:
    return get_tg().sent(method)[-1]


async def book(client, master_id: int, hour: int) -> int:
    r = await client.post(f"{API}/bookings", json={"branch_id": 1, "master_id": master_id, "service_ids": [1],
                                                    "starts_at": local(tomorrow(), hour).isoformat()})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_cancel_from_the_message_asks_once_more_tells_the_client_and_respects_masters(client, monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://bron.example.uz")
    await connect_bot()
    await link_staff("owner", "owner123", OWNER_CHAT)
    await link_staff("jasur", "barber123", JASUR_CHAT)  # master 1
    salon = await salon_row()

    jasurs = await book(client, 1, 11)
    owner_msg = [p for p in get_tg().sent() if p["chat_id"] == OWNER_CHAT][-1]
    rows = owner_msg["reply_markup"]["inline_keyboard"]
    assert rows[0][0]["callback_data"] == f"sx:{jasurs}"
    assert rows[1][0]["web_app"]["url"] == "https://bron.example.uz/premium/admin"
    jasur_msg = [p for p in get_tg().sent() if p["chat_id"] == JASUR_CHAT][-1]
    assert jasur_msg["reply_markup"]["inline_keyboard"][1][0]["web_app"]["url"] == "https://bron.example.uz/premium/panel"

    bekzods = await book(client, 2, 12)
    async with make_client() as c:
        # Jasur cannot cancel Bekzod's booking, a stranger nothing at all
        await tap(c, salon, JASUR_CHAT, f"sy:{bekzods}")
        assert last("answerCallbackQuery")["text"] == "Bu sizning broningiz emas"
        await tap(c, salon, 99999, f"sy:{jasurs}")
        assert last("answerCallbackQuery")["text"] == "Bu bron topilmadi"
        async with SessionLocal() as s:
            assert (await s.get(Booking, bekzods)).status == "confirmed" and (await s.get(Booking, jasurs)).status == "confirmed"

        # The owner taps "cancel": asked once more, "no" keeps it
        await tap(c, salon, OWNER_CHAT, f"sx:{jasurs}")
        assert last("editMessageReplyMarkup")["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"sy:{jasurs}"
        await tap(c, salon, OWNER_CHAT, f"sn:{jasurs}")
        async with SessionLocal() as s:
            assert (await s.get(Booking, jasurs)).status == "confirmed"
        # "yes": cancelled, the client and Jasur are told, the owner is not told about their own action
        before = len(to(OWNER_CHAT))
        await tap(c, salon, OWNER_CHAT, f"sy:{jasurs}")
        async with SessionLocal() as s:
            assert (await s.get(Booking, jasurs)).status == "cancelled"
        assert "bekor qilindi" in to(JASUR_CHAT)[-1]
        assert to(OWNER_CHAT)[before:] == ["‎❌ Bron bekor qilindi. Mijozga xabar yuborildi."]
        await tap(c, salon, OWNER_CHAT, f"sy:{jasurs}")  # a second tap changes nothing
        assert "faol emas" in last("answerCallbackQuery")["text"]


async def test_today_and_tomorrow_lists_show_a_master_only_their_own(client):
    await connect_bot()
    await link_staff("owner", "owner123", OWNER_CHAT)
    await link_staff("jasur", "barber123", JASUR_CHAT)
    salon = await salon_row()
    await book(client, 1, 11)
    await book(client, 2, 12)
    async with make_client() as c:
        await hook(c, salon, msg("📋 Ertaga", chat={"id": OWNER_CHAT, "first_name": "O"}))
        owners = to(OWNER_CHAT)[-1]
        assert "2 ta" in owners and "11:00" in owners and "12:00" in owners and "+998 90 111 11 11" in owners
        assert get_tg().sent()[-1]["reply_markup"]["keyboard"][0][0]["text"] == "📋 Bugun"
        await hook(c, salon, msg("/ertaga", chat={"id": JASUR_CHAT, "first_name": "J"}))
        jasurs = to(JASUR_CHAT)[-1]
        assert "1 ta" in jasurs and "11:00" in jasurs and "12:00" not in jasurs
        await hook(c, salon, msg("/bugun", chat={"id": JASUR_CHAT, "first_name": "J"}))
        assert "Bron yo'q" in to(JASUR_CHAT)[-1]
        # a client typing the same word gets the client answer, not the salon's list
        await hook(c, salon, msg("/bugun", chat=ME))
        assert "Bron yo'q" not in (to(ME["id"]) or [""])[-1] and "11:00" not in (to(ME["id"]) or [""])[-1]


async def test_the_panel_opened_from_the_bot_logs_in_by_telegram():
    await connect_bot()
    await link_staff("owner", "owner123", OWNER_CHAT)
    async with make_client() as c:
        assert (await c.post(f"{API}/staff/webapp", json={"init_data": sign_init(424242)})).status_code == 404
        assert (await c.post(f"{API}/staff/webapp", json={"init_data": sign_init(OWNER_CHAT).replace("Aziz", "X")})).status_code == 401
        r = await c.post(f"{API}/staff/webapp", json={"init_data": sign_init(OWNER_CHAT)})
        assert r.status_code == 200 and r.json()["role"] == "owner"
        assert (await c.get(f"{API}/staff/bookings", params={"date": str(tomorrow())})).status_code == 200
    async with make_client() as jasur:  # a master sees their own link state too
        await staff_login(jasur, "jasur", "barber123")
        state = (await jasur.get(f"{API}/staff/telegram/me")).json()
        assert state["connected"] and not state["me_linked"] and "?start=st" in state["me_link"]
