"""The flow a salon sees in a demo: a client subscribes in the bot and books inside it,
the owner, administrators and the booked master get a Telegram message; salons start from their kind's services."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Staff
from app.routers.telegram import staff_start_code
from app.security import hash_password
from app.telegram import get_tg
from tests.conftest import API, local, make_client, staff_login, tomorrow
from tests.test_telegram import ME, TOKEN, connect_bot, hook, msg, salon_row

OWNER_CHAT, JASUR_CHAT, MALIKA_CHAT = 7001, 7002, 7003


def sign_init(user_id: int, token: str = TOKEN, age: int = 0) -> str:
    """What Telegram hands a Mini App: fields signed with a key derived from the bot token."""
    fields = {"auth_date": str(int(time.time()) - age), "query_id": "AAH1", "user": json.dumps({"id": user_id, "first_name": "Aziz"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


async def link_staff(login: str, password: str, chat_id: int) -> dict:
    async with make_client() as c:
        await staff_login(c, login, password)
        state = (await c.get(f"{API}/staff/telegram")).json() if login in ("owner", "malika") else None
        if state is None:  # a master has no bot page; the link is built the same way for them
            async with SessionLocal() as s:
                staff = await s.scalar(select(Staff).where(Staff.login == login))
                code = staff_start_code(staff)
        else:
            assert state["connected"] and not state["me_linked"] and "?start=st" in state["me_link"]
            code = state["me_link"].split("start=")[1]
        who = {"id": chat_id, "first_name": login}
        await hook(c, await salon_row(), msg(f"/start {code}", chat=who))
        return (await c.get(f"{API}/staff/telegram")).json() if state is not None else {}


def to(chat_id: int) -> list[str]:
    return [p["text"] for p in get_tg().sent() if p["chat_id"] == chat_id]


async def test_owner_admin_and_the_booked_master_hear_about_new_bookings(client):
    await connect_bot()
    assert (await link_staff("owner", "owner123", OWNER_CHAT))["me_linked"] is True
    await link_staff("malika", "admin123", MALIKA_CHAT)
    await link_staff("jasur", "barber123", JASUR_CHAT)  # Jasur is master 1

    # A client books Bekzod (master 2): the owner and the administrator hear, Jasur does not.
    r = await client.post(f"{API}/bookings", json={"branch_id": 1, "master_id": 2, "service_ids": [1],
                                                    "starts_at": local(tomorrow(), 11).isoformat(), "comment": "Qisqaroq"})
    assert r.status_code == 201
    owner_msg = to(OWNER_CHAT)[-1]
    assert "Yangi bron" in owner_msg and "Soch olish" in owner_msg and "Bekzod" in owner_msg and "11:00" in owner_msg
    assert "+998 90 111 11 11" in owner_msg and "Qisqaroq" in owner_msg
    assert to(MALIKA_CHAT)[-1] == owner_msg
    assert not any("Yangi bron" in t for t in to(JASUR_CHAT))

    # A booking with Jasur reaches Jasur too.
    r = await client.post(f"{API}/bookings", json={"branch_id": 1, "master_id": 1, "service_ids": [1],
                                                    "starts_at": local(tomorrow(), 14).isoformat()})
    booking_id = r.json()["id"]
    assert "Jasur" in to(JASUR_CHAT)[-1] and "Yangi bron" in to(JASUR_CHAT)[-1]

    # The client cancels on the site: the salon is told.
    assert (await client.post(f"{API}/bookings/{booking_id}/cancel")).status_code == 200
    assert "bekor qilindi" in to(OWNER_CHAT)[-1] and "bekor qilindi" in to(JASUR_CHAT)[-1]

    # Malika books a caller herself: the others hear, Malika is not told about her own booking.
    before = len(to(MALIKA_CHAT))
    async with make_client() as admin:
        await staff_login(admin, "malika", "admin123")
        r = await admin.post(f"{API}/staff/bookings", json={"branch_id": 1, "master_id": 1, "service_ids": [1],
                                                            "starts_at": local(tomorrow(), 16).isoformat(), "client_phone": "+998905555555", "client_name": "Laylo"})
        assert r.status_code == 201
    assert "Laylo" in to(OWNER_CHAT)[-1] and "Laylo" in to(JASUR_CHAT)[-1]
    assert len(to(MALIKA_CHAT)) == before

    # Switching notices off stops them.
    async with make_client() as owner:
        await staff_login(owner, "owner", "owner123")
        assert (await owner.delete(f"{API}/staff/telegram/me")).json()["me_linked"] is False
    count = len(to(OWNER_CHAT))
    await client.post(f"{API}/bookings", json={"branch_id": 1, "master_id": 2, "service_ids": [1], "starts_at": local(tomorrow(), 17).isoformat()})
    assert len(to(OWNER_CHAT)) == count


async def test_a_forged_or_outdated_staff_link_links_nothing():
    await connect_bot()
    salon = await salon_row()
    async with make_client() as c:
        await hook(c, salon, msg("/start st1_" + "0" * 24, chat={"id": 9001, "first_name": "X"}))
    async with SessionLocal() as s:
        assert (await s.scalar(select(Staff).where(Staff.login == "owner"))).tg_chat_id is None
    assert "eskirgan" in to(9001)[-1]

    # A link made before a password change no longer works.
    async with SessionLocal() as s:
        owner = await s.scalar(select(Staff).where(Staff.login == "owner"))
        old = staff_start_code(owner)
        owner.token_version += 1
        await s.commit()
    async with make_client() as c:
        await hook(c, salon, msg(f"/start {old}", chat={"id": 9002, "first_name": "X"}))
    async with SessionLocal() as s:
        assert (await s.scalar(select(Staff).where(Staff.login == "owner"))).tg_chat_id is None


async def test_client_subscribes_in_the_bot_then_books_inside_it_without_a_second_login(monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://bron.example.uz")
    assert (await connect_bot()).status_code == 200
    menu = [p for _, m, p in get_tg().calls if m == "setChatMenuButton"][-1]
    assert menu["menu_button"]["web_app"]["url"] == "https://bron.example.uz/premium"
    salon = await salon_row()
    async with make_client() as c:
        # /start: a new chat is asked for its number
        await hook(c, salon, msg("/start"))
        assert to(ME["id"])[-1].startswith("Assalomu alaykum") and get_tg().sent()[-1]["reply_markup"]["keyboard"][0][0]["request_contact"]
        await hook(c, salon, msg(contact={"phone_number": "998901234567", "first_name": "Aziz", "user_id": ME["id"]}))
        button = get_tg().sent()[-1]["reply_markup"]["inline_keyboard"][0][0]
        assert button["web_app"]["url"] == "https://bron.example.uz/premium"
        # next /start: already subscribed, straight to the button
        await hook(c, salon, msg("/start"))
        assert "Aziz" in to(ME["id"])[-1] and get_tg().sent()[-1]["reply_markup"]["inline_keyboard"][0][0]["web_app"]

    async with make_client() as app_view:  # the page opened inside Telegram
        r = await app_view.post(f"{API}/auth/webapp", json={"init_data": sign_init(ME["id"])})
        assert r.status_code == 200 and r.json()["phone"] == "+998901234567"
        assert (await app_view.get(f"{API}/auth/me")).status_code == 200
        r = await app_view.post(f"{API}/bookings", json={"branch_id": 1, "master_id": 1, "service_ids": [1],
                                                          "starts_at": local(tomorrow(), 12).isoformat()})
        assert r.status_code == 201
        assert "Bron tasdiqlandi" in to(ME["id"])[-1] and "Usta: Jasur" in to(ME["id"])[-1]


async def test_mini_app_login_needs_a_genuine_fresh_signature_from_this_salons_bot():
    await connect_bot()
    salon = await salon_row()
    async with make_client() as c:
        await hook(c, salon, msg(contact={"phone_number": "998901234567", "first_name": "Aziz", "user_id": ME["id"]}))
        tampered = sign_init(ME["id"]).replace("Aziz", "Boshqa")
        assert (await c.post(f"{API}/auth/webapp", json={"init_data": tampered})).status_code == 401
        other_bot = sign_init(ME["id"], token="987654321:AAH-another_bot_token_000000000000")
        assert (await c.post(f"{API}/auth/webapp", json={"init_data": other_bot})).status_code == 401
        assert (await c.post(f"{API}/auth/webapp", json={"init_data": sign_init(ME["id"], age=2 * 86400)})).status_code == 401
        assert (await c.post(f"{API}/auth/webapp", json={"init_data": sign_init(424242)})).status_code == 404  # never shared a number
        assert (await c.get(f"{API}/auth/me")).status_code == 401


async def test_a_beauty_salon_starts_with_its_own_services_and_sample_masters(monkeypatch):
    monkeypatch.setattr(settings, "platform_login", "boss")
    monkeypatch.setattr(settings, "platform_password_hash", hash_password("platform-pass"))
    async with make_client() as p:
        await p.post("/api/platform/login", json={"login": "boss", "password": "platform-pass"})
        body = {"slug": "gulnoza", "name": "Gulnoza Beauty", "months": 1, "owner_name": "Gulnoza", "owner_login": "gulnoza",
                "owner_password": "gulnoza-2026", "kind": "beauty", "sample_masters": True}
        r = await p.post("/api/platform/salons", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["kind"] == "beauty"
        assert (await p.post("/api/platform/salons", json={**body, "slug": "x-salon", "kind": "avto"})).status_code == 422
    api = "/api/s/gulnoza"
    async with make_client() as c:
        info = (await c.get(api)).json()
        assert info["kind"] == "beauty"
        branch = (await c.get(f"{api}/branches")).json()[0]["id"]
        names = {s["name"] for s in (await c.get(f"{api}/branches/{branch}/services")).json()}
        assert "Manikyur + gel lak" in names and "Soch olish" not in names
        masters = (await c.get(f"{api}/masters")).json()
        assert [m["name"] for m in masters] == ["Dilnoza", "Madina", "Sevara"]
    async with make_client() as madina:  # sample masters log in with their name and the owner's password
        me = await staff_login(madina, "madina", "gulnoza-2026", api=api)
        assert me["role"] == "barber" and me["salon"]["slug"] == "gulnoza"
