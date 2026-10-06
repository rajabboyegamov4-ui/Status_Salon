"""Salons are fully separate: data, logins and subscriptions."""

from datetime import timedelta

from app.config import settings
from app.models import Salon
from app.security import hash_password
from app.tenancy import salon_today
from scripts.seed import seed_salon
from tests.conftest import API, local, login, make_client, staff_login, tomorrow

OTHER = "/api/s/navruz"


async def second_salon(session):
    # Same staff logins ("jasur", "owner"...) in a different salon must not collide.
    salon = await seed_salon(session, slug="navruz", name="Navruz Barber", branch_name="Navruz, Yunusobod")
    return salon


async def test_salons_do_not_see_each_other(client, session):
    other = await second_salon(session)
    r = await client.post(
        f"{API}/bookings",
        json={"branch_id": 1, "master_id": 1, "service_ids": [1], "starts_at": local(tomorrow(), 11).isoformat()},
    )
    assert r.status_code == 201
    booking_id = r.json()["id"]

    other_branches = (await client.get(f"{OTHER}/branches")).json()
    assert [b["name"] for b in other_branches] == ["Navruz, Yunusobod"]
    other_branch = other_branches[0]["id"]
    # Salon A's branch is not reachable through salon B's address, and vice versa.
    assert (await client.get(f"{OTHER}/branches/1/services")).status_code == 404
    assert (await client.get(f"{API}/branches/{other_branch}/services")).status_code == 404

    # A client session from salon A is not valid in salon B.
    assert (await client.get(f"{OTHER}/auth/me")).status_code == 401

    async with make_client() as jasur_b:
        me = await staff_login(jasur_b, "jasur", "barber123", api=OTHER)
        assert me["salon"]["slug"] == "navruz"
        assert (await jasur_b.get(f"{OTHER}/staff/bookings", params={"date": str(tomorrow())})).json() == []
        # Salon B's staff token does not open salon A's panel.
        assert (await jasur_b.get(f"{API}/staff/bookings", params={"date": str(tomorrow())})).status_code == 401
        r = await jasur_b.patch(f"{OTHER}/staff/bookings/{booking_id}", json={"status": "no_show"})
        assert r.status_code == 404

    # Booking salon A's master through salon B is refused.
    async with make_client() as c:
        await login(c, "+998907777777", api=OTHER)
        r = await c.post(
            f"{OTHER}/bookings",
            json={"branch_id": 1, "master_id": 1, "service_ids": [1], "starts_at": local(tomorrow(), 12).isoformat()},
        )
        assert r.status_code == 422
    assert other.id != 1


async def test_unknown_salon_is_404():
    async with make_client() as c:
        assert (await c.get("/api/s/yoq-salon/branches")).status_code == 404
        assert (await c.get("/yoq.salon")).status_code == 404


async def test_lapsed_subscription_stops_booking_but_panel_still_reads(client, session):
    r = await client.post(
        f"{API}/bookings",
        json={"branch_id": 1, "master_id": 1, "service_ids": [1], "starts_at": local(tomorrow(), 11).isoformat()},
    )
    booking_id = r.json()["id"]
    salon = await session.get(Salon, 1)
    salon.paid_until = salon_today() - timedelta(days=1)
    await session.commit()

    assert (await client.get(API)).json()["active"] is False
    r = await client.post(
        f"{API}/bookings",
        json={"branch_id": 1, "master_id": 1, "service_ids": [1], "starts_at": local(tomorrow(), 13).isoformat()},
    )
    assert r.status_code == 402
    async with make_client() as c:
        assert (await c.post(f"{API}/auth/request-code", json={"phone": "+998906666666"})).status_code == 402
    async with make_client() as admin:
        me = await staff_login(admin, "malika", "admin123")
        assert me["salon"]["active"] is False
        assert len((await admin.get(f"{API}/staff/bookings", params={"date": str(tomorrow())})).json()) == 1
        assert (await admin.patch(f"{API}/staff/bookings/{booking_id}", json={"status": "no_show"})).status_code == 402


async def test_platform_creates_and_extends_salon(monkeypatch):
    monkeypatch.setattr(settings, "platform_login", "boss")
    monkeypatch.setattr(settings, "platform_password_hash", hash_password("platform-pass"))
    async with make_client() as p:
        assert (await p.get("/api/platform/salons")).status_code == 401
        assert (await p.post("/api/platform/login", json={"login": "boss", "password": "bad"})).status_code == 401
        assert (await p.post("/api/platform/login", json={"login": "boss", "password": "platform-pass"})).status_code == 204

        body = {"slug": "Baraka", "name": "Baraka Barber", "months": 1, "owner_name": "Aziz", "owner_login": "aziz",
                "owner_password": "aziz-2026"}
        r = await p.post("/api/platform/salons", json=body)
        assert r.status_code == 201, r.text
        new = r.json()
        assert new["slug"] == "baraka" and new["active"] is True
        assert (await p.post("/api/platform/salons", json=body)).status_code == 409
        assert (await p.post("/api/platform/salons", json={**body, "slug": "api"})).status_code == 422

        r = await p.patch(f"/api/platform/salons/{new['id']}", json={"is_active": False})
        assert r.json()["active"] is False
        r = await p.patch(f"/api/platform/salons/{new['id']}", json={"is_active": True, "extend_months": 2})
        assert r.json()["active"] is True and r.json()["paid_until"] > new["paid_until"]
        assert {s["slug"] for s in (await p.get("/api/platform/salons")).json()} == {"premium", "baraka"}

    # The new salon's owner logs in right away and its site has services.
    async with make_client() as owner:
        await staff_login(owner, "aziz", "aziz-2026", api="/api/s/baraka")
        branches = (await owner.get("/api/s/baraka/branches")).json()
        assert len((await owner.get(f"/api/s/baraka/branches/{branches[0]['id']}/services")).json()) == 4
