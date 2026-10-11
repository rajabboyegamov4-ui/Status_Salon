"""The platform's own people: owner, partner, sales; leads from first call to payment; reusing demo bots."""

import pytest

from app.config import settings
from app.security import hash_password
from app.telegram import get_tg
from tests.conftest import make_client
from tests.test_telegram import connect_bot, salon_row

P = "/api/platform"


@pytest.fixture
def boss(monkeypatch):
    monkeypatch.setattr(settings, "platform_login", "boss")
    monkeypatch.setattr(settings, "platform_password_hash", hash_password("platform-pass"))


async def signed(login: str, password: str):
    c = make_client()
    await c.__aenter__()
    r = await c.post(f"{P}/login", json={"login": login, "password": password})
    assert r.status_code == 204, r.text
    return c


def demo_body(slug: str, **extra) -> dict:
    return {"slug": slug, "name": slug.title(), "months": 3, "owner_name": "Ega", "owner_login": "owner",
            "owner_password": "demo-pass-1", "kind": "beauty", "sample_masters": True, **extra}


async def test_roles_sales_sees_own_leads_opens_demos_but_not_money(boss):
    owner = await signed("boss", "platform-pass")
    assert (await owner.get(f"{P}/me")).json()["role"] == "owner"
    r = await owner.post(f"{P}/team", json={"login": "ali", "name": "Ali", "password": "sherik-2026", "role": "partner"})
    assert r.status_code == 201
    r = await owner.post(f"{P}/team", json={"login": "nodira", "name": "Nodira", "password": "sotuv-2026", "role": "sales"})
    nodira_id = r.json()["id"]
    await owner.post(f"{P}/team", json={"login": "bobur", "name": "Bobur", "password": "sotuv-2027", "role": "sales"})

    partner = await signed("ali", "sherik-2026")
    assert (await partner.post(f"{P}/team", json={"login": "x2", "name": "X", "password": "partner-22", "role": "partner"})).status_code == 403
    assert (await partner.get(f"{P}/stats")).json()["income_30d"] == 0

    nodira = await signed("nodira", "sotuv-2026")
    bobur = await signed("bobur", "sotuv-2027")
    lead = (await nodira.post(f"{P}/leads", json={"name": "Gulnoza Beauty", "kind": "beauty", "phone": "+998901112233",
                                                    "next_step": "Demo ko'rsatish"})).json()
    assert lead["assigned_to"] == nodira_id and lead["assigned_name"] == "Nodira"
    assert [x["name"] for x in (await bobur.get(f"{P}/leads")).json()] == []  # not Bobur's lead
    assert (await bobur.patch(f"{P}/leads/{lead['id']}", json={"note": "x"})).status_code == 404
    assert len((await owner.get(f"{P}/leads")).json()) == 1  # the owner sees everyone's

    # Nodira opens the demo straight from her lead: always a demo with a 14-day trial, never paid months
    r = await nodira.post(f"{P}/salons", json=demo_body("gulnoza", lead_id=lead["id"]))
    assert r.status_code == 201, r.text
    demo = r.json()
    assert demo["is_demo"] and demo["paid_total"] is None and demo["lead_id"] == lead["id"]
    lead_now = (await nodira.get(f"{P}/leads")).json()[0]
    assert lead_now["status"] == "demo" and lead_now["salon_slug"] == "gulnoza"
    assert (await nodira.post(f"{P}/salons", json=demo_body("gulnoza2", lead_id=lead["id"]))).status_code == 409

    # Money is for managers only
    assert (await nodira.patch(f"{P}/salons/{demo['id']}", json={"extend_months": 1, "amount": 300000})).status_code == 403
    assert (await nodira.get(f"{P}/payments")).status_code == 403
    assert "income_30d" not in (await nodira.get(f"{P}/stats")).json()
    assert (await nodira.patch(f"{P}/leads/{lead['id']}", json={"status": "paid"})).status_code == 403

    # The owner takes the payment: no longer a demo, the lead is paid, income counts
    r = await owner.patch(f"{P}/salons/{demo['id']}", json={"extend_months": 1, "amount": 300000})
    assert r.json()["is_demo"] is False and r.json()["paid_total"] == 300000
    assert (await owner.get(f"{P}/leads")).json()[0]["status"] == "paid"
    stats = (await owner.get(f"{P}/stats")).json()
    assert stats["income_30d"] == 300000 and stats["paying"] >= 1
    assert (await owner.get(f"{P}/payments")).json()[0]["salon_name"] == "Gulnoza"

    # A switched-off sales person is out at once
    await owner.patch(f"{P}/team/{nodira_id}", json={"is_active": False})
    assert (await nodira.get(f"{P}/leads")).status_code == 401
    for c in (owner, partner, nodira, bobur):
        await c.__aexit__(None, None, None)


async def test_a_demo_bot_moves_to_the_next_demo_but_never_leaves_a_paying_business(boss):
    owner = await signed("boss", "platform-pass")
    a = (await owner.post(f"{P}/salons", json=demo_body("demo-a", is_demo=True))).json()
    b = (await owner.post(f"{P}/salons", json=demo_body("demo-b", is_demo=True))).json()
    # demo-a gets a bot the usual way, from its admin panel
    assert (await connect_bot(api="/api/s/demo-a", login="owner", password="demo-pass-1")).status_code == 200
    r = await owner.post(f"{P}/salons/{b['id']}/take-bot", json={"from_salon_id": a["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["telegram_bot"] == "demo_salon_bot"
    salon_a, salon_b = await salon_row("demo-a"), await salon_row("demo-b")
    assert salon_a.tg_token_sealed is None and salon_b.tg_bot_username == "demo_salon_bot"
    hook = [p for _, m, p in get_tg().calls if m == "setWebhook"][-1]
    assert hook["url"].endswith(f"/api/tg/{salon_b.id}") and hook["secret_token"] == salon_b.tg_webhook_secret

    # once demo-b pays, its bot stays with it
    await owner.patch(f"{P}/salons/{b['id']}", json={"extend_months": 1, "amount": 500000})
    r = await owner.post(f"{P}/salons/{a['id']}/take-bot", json={"from_salon_id": b["id"]})
    assert r.status_code == 409
    await owner.__aexit__(None, None, None)


async def test_wrong_platform_password_and_unknown_login(boss):
    async with make_client() as c:
        assert (await c.post(f"{P}/login", json={"login": "boss", "password": "x"})).status_code == 401
        assert (await c.post(f"{P}/login", json={"login": "nobody", "password": "x"})).status_code == 401
        assert (await c.get(f"{P}/leads")).status_code == 401
