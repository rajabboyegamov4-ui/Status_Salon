"""Orders module: a flower or cake shop takes orders in its bot; staff move them on from Telegram or the desk."""

from datetime import timedelta

from app.config import settings
from app.db import SessionLocal
from app.models import Order
from app.services.team import create_salon
from app.telegram import get_tg
from tests.conftest import API, make_client, staff_login, tomorrow
from tests.test_demo_flow import sign_init, to
from tests.test_telegram import connect_bot, hook, msg, salon_row

G = "/api/s/gul"
OWNER_CHAT, CLIENT = 8101, {"id": 8202, "first_name": "Malika"}


async def shop_with_bot() -> None:
    async with SessionLocal() as s:
        await create_salon(s, slug="gul", name="Gul Do'kon", paid_until=tomorrow() + timedelta(days=30), kind="shop",
                           sample_masters=True, owner_name="Gulchehra", owner_login="gul", owner_password="gul-2026")
        await s.commit()
    assert (await connect_bot(api=G, login="gul", password="gul-2026")).status_code == 200
    salon = await salon_row("gul")
    async with make_client() as c:
        await staff_login(c, "gul", "gul-2026", api=G)
        link = (await c.get(f"{G}/staff/telegram")).json()["me_link"]
        await hook(c, salon, msg(f"/start {link.split('start=')[1]}", chat={"id": OWNER_CHAT, "first_name": "G"}))
        await hook(c, salon, msg(contact={"phone_number": "998935554433", "first_name": "Malika", "user_id": CLIENT["id"]}, chat=CLIENT))


async def client() -> object:
    c = make_client()
    await c.__aenter__()
    assert (await c.post(f"{G}/auth/webapp", json={"init_data": sign_init(CLIENT["id"])})).status_code == 200
    return c


def tap(c, salon, who, data):
    return hook(c, salon, {"update_id": 5, "callback_query": {"id": "q", "from": {"id": who}, "data": data,
                                                              "message": {"message_id": 3, "chat": {"id": who}}}})


async def test_a_shop_order_from_the_bot_to_the_door(monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://bron.example.uz")
    await shop_with_bot()
    salon = await salon_row("gul")
    menu = [p for _, m, p in get_tg().calls if m == "setChatMenuButton"][-1]["menu_button"]
    assert menu["text"] == "Buyurtma" and menu["web_app"]["url"] == "https://bron.example.uz/gul/shop"

    c = await client()
    info = (await c.get(G)).json()
    assert info["modules"] == ["orders"]
    cat = (await c.get(f"{G}/catalog")).json()
    assert len(cat["products"]) == 5 and cat["shop"]["pickup"] and cat["shop"]["delivery"]
    rose, cake = cat["products"][0], cat["products"][2]

    body = {"items": [{"product_id": rose["id"], "qty": 1}, {"product_id": cake["id"], "qty": 2}], "fulfillment": "delivery",
            "address": "Chilonzor 9, 12-uy", "comment": "Otkritka: Tabriklaymiz!"}
    assert (await c.post(f"{G}/orders", json={**body, "address": ""})).status_code == 422  # delivery needs an address
    r = await c.post(f"{G}/orders", json=body, headers={"Idempotency-Key": "k1"})
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["number"] == 1 and o["total"] == rose["price"] + 2 * cake["price"] and o["status"] == "new"
    again = await c.post(f"{G}/orders", json=body, headers={"Idempotency-Key": "k1"})
    assert again.status_code == 200 and again.json()["id"] == o["id"]  # a second tap orders nothing new

    assert "Buyurtma #1 qabul qilindi" in to(CLIENT["id"])[-1] and "Chilonzor 9" in to(CLIENT["id"])[-1]
    staff_msg = [p for p in get_tg().sent() if p["chat_id"] == OWNER_CHAT][-1]
    assert "Yangi buyurtma #1" in staff_msg["text"] and "+998 93 555 44 33" in staff_msg["text"]
    assert [b["callback_data"] for b in staff_msg["reply_markup"]["inline_keyboard"][0]] == [f"oa:{o['id']}", f"ox:{o['id']}"]

    async with make_client() as bot:
        await tap(bot, salon, OWNER_CHAT, f"oa:{o['id']}")
        assert "tasdiqlandi" in to(CLIENT["id"])[-1]
        # the client can no longer cancel an accepted order
        assert (await c.post(f"{G}/orders/{o['id']}/cancel")).status_code == 422
        await tap(bot, salon, OWNER_CHAT, f"or:{o['id']}")
        assert "tayyor" in to(CLIENT["id"])[-1] and "Kuryerga" in to(CLIENT["id"])[-1]
        await tap(bot, salon, OWNER_CHAT, f"ov:{o['id']}")
        await tap(bot, salon, OWNER_CHAT, f"od:{o['id']}")
        assert "berildi" in to(CLIENT["id"])[-1]
        await tap(bot, salon, 99999, f"oa:{o['id']}")  # a stranger
        assert get_tg().sent("answerCallbackQuery")[-1]["text"] == "Bu buyurtma topilmadi"
    async with SessionLocal() as s:
        assert (await s.get(Order, o["id"])).status == "done"

    mine = (await c.get(f"{G}/me/orders")).json()
    assert mine[0]["status"] == "done" and mine[0]["status_label"] == "Berildi"
    # a new order the client cancels while it is still new; the owner hears
    r = await c.post(f"{G}/orders", json={"items": [{"product_id": rose["id"], "qty": 1}], "fulfillment": "pickup"})
    assert r.json()["number"] == 2
    assert (await c.post(f"{G}/orders/{r.json()['id']}/cancel")).json()["status"] == "cancelled"
    assert "bekor qilindi" in to(OWNER_CHAT)[-1]
    await c.__aexit__(None, None, None)


async def test_the_orders_desk_catalog_and_settings():
    await shop_with_bot()
    c = await client()
    cat = (await c.get(f"{G}/catalog")).json()["products"]
    await c.post(f"{G}/orders", json={"items": [{"product_id": cat[1]["id"], "qty": 3}], "fulfillment": "pickup"})
    async with make_client() as desk:
        await staff_login(desk, "gul", "gul-2026", api=G)
        open_ = (await desk.get(f"{G}/staff/orders")).json()
        assert len(open_) == 1 and open_[0]["client_name"] == "Malika" and open_[0]["client_phone"] == "+998935554433"
        oid = open_[0]["id"]
        assert (await desk.patch(f"{G}/staff/orders/{oid}", json={"status": "done"})).status_code == 409  # skip a step: refused
        assert (await desk.patch(f"{G}/staff/orders/{oid}", json={"status": "accepted"})).json()["status"] == "accepted"
        assert (await desk.patch(f"{G}/staff/orders/{oid}", json={"status": "ready"})).status_code == 200
        assert (await desk.patch(f"{G}/staff/orders/{oid}", json={"status": "delivering"})).status_code == 409  # pickup order
        assert (await desk.patch(f"{G}/staff/orders/{oid}", json={"status": "done"})).status_code == 200
        assert (await desk.get(f"{G}/staff/orders")).json() == []
        assert len((await desk.get(f"{G}/staff/orders", params={"scope": "closed"})).json()) == 1

        p = (await desk.post(f"{G}/staff/products", json={"name": "Lola (11 dona)", "price": 150000, "category": "Guldastalar"})).json()
        jpeg = b"\xff\xd8\xff\xe0" + b"0" * 200
        r = await desk.post(f"{G}/staff/products/{p['id']}/photo", content=jpeg, headers={"Content-Type": "image/jpeg"})
        assert r.status_code == 200 and r.json()["photo_url"].startswith("/media/")
        assert (await desk.get(r.json()["photo_url"])).status_code == 200
        assert (await desk.patch(f"{G}/staff/products/{p['id']}", json={"is_active": False})).json()["is_active"] is False
        assert all(x["id"] != p["id"] for x in (await c.get(f"{G}/catalog")).json()["products"])  # sold out: hidden
        r = await c.post(f"{G}/orders", json={"items": [{"product_id": p["id"], "qty": 1}], "fulfillment": "pickup"})
        assert r.status_code == 422  # and cannot be ordered

        assert (await desk.patch(f"{G}/staff/shop", json={"pickup": False, "delivery": False})).status_code == 422
        s = (await desk.patch(f"{G}/staff/shop", json={"delivery_fee": 20000, "min_order": 500000})).json()
        assert s["delivery_fee"] == 20000
    r = await c.post(f"{G}/orders", json={"items": [{"product_id": cat[3]["id"], "qty": 1}], "fulfillment": "pickup"})
    assert r.status_code == 422 and "Eng kam" in r.json()["detail"]
    r = await c.post(f"{G}/orders", json={"items": [{"product_id": cat[0]["id"], "qty": 2}], "fulfillment": "delivery", "address": "Yunusobod 4"})
    assert r.json()["delivery_fee"] == 20000 and r.json()["total"] == 2 * cat[0]["price"] + 20000
    await c.__aexit__(None, None, None)


async def test_orders_stay_inside_their_business():
    await shop_with_bot()
    async with make_client() as c:
        assert (await c.get(f"{API}/catalog")).status_code == 404  # premium only books
        await staff_login(c, "owner", "owner123")
        assert (await c.get(f"{API}/staff/orders")).status_code == 404
        gul_product = (await c.get(f"{G}/catalog")).json()["products"][0]["id"]
        # premium's owner session does not open the shop's desk
        assert (await c.get(f"{G}/staff/orders")).status_code == 401
    async with make_client() as premium_client:  # premium's product ids are not the shop's
        await hook(premium_client, await salon_row("gul"), msg(contact={"phone_number": "998901110000", "first_name": "A", "user_id": 777}, chat={"id": 777, "first_name": "A"}))
        await premium_client.post(f"{G}/auth/webapp", json={"init_data": sign_init(777)})
        r = await premium_client.post(f"{G}/orders", json={"items": [{"product_id": gul_product + 999, "qty": 1}], "fulfillment": "pickup"})
        assert r.status_code == 422
