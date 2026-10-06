"""Each salon's own Telegram bot: connecting it, logging in through it, booking messages and buttons."""

from datetime import timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Booking, Review, Salon
from app.services.team import create_salon
from app.telegram import get_tg, unseal
from tests.conftest import API, local, make_client, staff_login, tomorrow
from tests.test_staff import move_to_past

TOKEN = "123456789:AAH-fake_token_for_tests_000000000000"
ME = {"id": 555, "first_name": "Aziz"}


async def connect_bot(api=API, login="owner", password="owner123", token=TOKEN):
    async with make_client() as owner:
        await staff_login(owner, login, password, api=api)
        return await owner.put(f"{api}/staff/telegram", json={"token": token})


async def salon_row(slug="premium") -> Salon:
    async with SessionLocal() as s:
        return await s.scalar(select(Salon).where(Salon.slug == slug))


def hook(c, salon, update, secret=None):
    return c.post(f"/api/tg/{salon.id}", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": secret or salon.tg_webhook_secret})


def msg(text=None, contact=None, chat=ME):
    m = {"message_id": 1, "chat": {"id": chat["id"], "type": "private"}, "from": chat}
    if text is not None:
        m["text"] = text
    if contact:
        m["contact"] = contact
    return {"update_id": 1, "message": m}


async def telegram_login(c, phone="+998901234567", chat=ME):
    """The whole client login: site -> bot /start -> share contact -> site gets the session."""
    start = (await c.post(f"{API}/auth/telegram")).json()
    salon = await salon_row()
    assert (await hook(c, salon, msg(f"/start {start['token']}", chat=chat))).status_code == 200
    contact = {"phone_number": phone.lstrip("+"), "first_name": chat["first_name"], "user_id": chat["id"]}
    await hook(c, salon, msg(contact=contact, chat=chat))
    r = await c.get(f"{API}/auth/telegram/{start['token']}")
    assert r.status_code == 200 and r.json()["status"] == "done", r.text
    return r.json()["user"], start["token"]


async def test_only_the_owner_connects_a_real_looking_token_and_it_is_stored_encrypted():
    async with make_client() as admin:
        await staff_login(admin, "malika", "admin123")
        assert (await admin.put(f"{API}/staff/telegram", json={"token": TOKEN})).status_code == 403
    assert (await connect_bot(token="not-a-token")).status_code == 422
    r = await connect_bot()
    assert r.status_code == 200 and r.json()["connected"] and r.json()["username"]
    salon = await salon_row()
    assert TOKEN not in (salon.tg_token_sealed or "") and unseal(salon.tg_token_sealed) == TOKEN
    hooks = [p for _, m, p in get_tg().calls if m == "setWebhook"]
    assert hooks[-1]["url"].endswith(f"/api/tg/{salon.id}") and hooks[-1]["secret_token"] == salon.tg_webhook_secret
    async with make_client() as c:
        assert (await c.get(API)).json()["telegram_bot"] == salon.tg_bot_username


async def test_one_bot_cannot_serve_two_salons_and_secrets_do_not_cross():
    assert (await connect_bot()).status_code == 200
    async with SessionLocal() as s:
        await create_salon(s, slug="boshqa", name="Boshqa", paid_until=tomorrow() + timedelta(days=30),
                           owner_name="O", owner_login="o", owner_password="owner-123")
        await s.commit()
    r = await connect_bot(api="/api/s/boshqa", login="o", password="owner-123")
    assert r.status_code == 409  # same bot username as premium
    premium, other = await salon_row(), await salon_row("boshqa")
    async with make_client() as c:
        # premium's secret does not open another salon's address, and a guess opens nothing
        assert (await hook(c, other, msg("/start x"), secret=premium.tg_webhook_secret)).status_code == 403
        assert (await hook(c, premium, msg("/start x"), secret="guess")).status_code == 403


async def test_client_logs_in_by_sharing_own_number_once():
    await connect_bot()
    async with make_client() as c:
        start = (await c.post(f"{API}/auth/telegram")).json()
        assert start["url"].startswith("https://t.me/") and start["token"] in start["url"]
        assert (await c.get(f"{API}/auth/telegram/{start['token']}")).json()["status"] == "pending"
        salon = await salon_row()
        await hook(c, salon, msg(f"/start {start['token']}"))
        # Someone else's contact is refused.
        await hook(c, salon, msg(contact={"phone_number": "998907777777", "first_name": "X", "user_id": 999}))
        assert (await c.get(f"{API}/auth/telegram/{start['token']}")).json()["status"] == "pending"
        await hook(c, salon, msg(contact={"phone_number": "998901234567", "first_name": "Aziz", "user_id": ME["id"]}))
        r = await c.get(f"{API}/auth/telegram/{start['token']}")
        assert r.json()["status"] == "done" and r.json()["user"]["phone"] == "+998901234567"
        assert (await c.get(f"{API}/auth/me")).status_code == 200
        assert (await c.get(f"{API}/auth/telegram/{start['token']}")).status_code == 410  # one link, one browser

    async with make_client() as again:  # next time the bot knows this chat: one tap, no contact needed
        start = (await again.post(f"{API}/auth/telegram")).json()
        salon = await salon_row()
        await hook(again, salon, msg(f"/start {start['token']}"))
        # not logged in until the client taps the confirm button (a link sent by someone else gets nothing)
        assert (await again.get(f"{API}/auth/telegram/{start['token']}")).json()["status"] == "pending"
        button = get_tg().sent()[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        assert button == f"login:{start['token']}"
        stranger = {"id": "cq0", "from": {"id": 999}, "data": button}
        await hook(again, salon, {"update_id": 2, "callback_query": stranger})
        assert (await again.get(f"{API}/auth/telegram/{start['token']}")).json()["status"] == "pending"
        await hook(again, salon, {"update_id": 3, "callback_query": {"id": "cq1", "from": ME, "data": button}})
        assert (await again.get(f"{API}/auth/telegram/{start['token']}")).json()["status"] == "done"


async def test_booking_messages_and_buttons_work_only_for_their_owner():
    await connect_bot()
    async with make_client() as c:
        await telegram_login(c)
        start = local(tomorrow() + timedelta(days=1), 15)
        r = await c.post(f"{API}/bookings", json={"branch_id": 1, "master_id": 2, "service_ids": [1], "starts_at": start.isoformat()})
        assert r.status_code == 201
        booking_id = r.json()["id"]
        sent = get_tg().sent()[-1]
        assert sent["chat_id"] == ME["id"] and "Bron tasdiqlandi" in sent["text"]
        assert sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"cancel:{booking_id}"

        salon = await salon_row()
        tap = lambda who, data: hook(c, salon, {"update_id": 2, "callback_query": {"id": "q", "from": who, "data": data}})  # noqa: E731
        await tap({"id": 999, "first_name": "Begona"}, f"cancel:{booking_id}")
        async with SessionLocal() as s:
            assert (await s.get(Booking, booking_id)).status == "confirmed"
        await tap(ME, f"cancel:{booking_id}")
        async with SessionLocal() as s:
            assert (await s.get(Booking, booking_id)).status == "cancelled"


async def test_after_the_visit_the_bot_asks_for_stars():
    await connect_bot()
    async with make_client() as c:
        await telegram_login(c)
        r = await c.post(f"{API}/bookings", json={"branch_id": 1, "master_id": 1, "service_ids": [1], "starts_at": local(tomorrow(), 16).isoformat()})
        booking_id = r.json()["id"]
        await move_to_past(booking_id)
        async with make_client() as jasur:
            await staff_login(jasur, "jasur", "barber123")
            assert (await jasur.patch(f"{API}/staff/bookings/{booking_id}", json={"status": "completed"})).status_code == 200
        ask = get_tg().sent()[-1]
        assert "baholang" in ask["text"] and ask["reply_markup"]["inline_keyboard"][0][4]["callback_data"] == f"rate:{booking_id}:5"
        await hook(c, await salon_row(), {"update_id": 3, "callback_query": {"id": "q", "from": ME, "data": f"rate:{booking_id}:5"}})
        async with SessionLocal() as s:
            assert (await s.scalar(select(Review).where(Review.booking_id == booking_id))).rating == 5


async def test_no_bot_means_no_telegram_login():
    async with make_client() as c:
        assert (await c.post(f"{API}/auth/telegram")).status_code == 409
