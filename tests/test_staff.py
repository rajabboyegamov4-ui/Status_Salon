from datetime import timedelta

from sqlalchemy import update

from app.db import SessionLocal
from app.models import Booking
from app.sms import ConsoleSms
from tests.conftest import API, local, login, make_client, staff_login, tomorrow


async def move_to_past(booking_id, days=2):
    """Pretend the visit already happened."""
    async with SessionLocal() as s:
        shift = timedelta(days=days)
        await s.execute(
            update(Booking).where(Booking.id == booking_id).values(starts_at=Booking.starts_at - shift, ends_at=Booking.ends_at - shift)
        )
        await s.commit()


def day_params():
    return {"date": str(tomorrow())}


async def book_as_client(client, hh, master_id):
    r = await client.post(
        f"{API}/bookings",
        json={"branch_id": 1, "master_id": master_id, "service_ids": [1], "starts_at": local(tomorrow(), hh).isoformat()},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_wrong_code_and_cooldown():
    async with make_client() as c:
        assert (await c.post(f"{API}/auth/request-code", json={"phone": "90 123 45 67"})).status_code == 204
        assert ConsoleSms.sent[-1][0] == "+998901234567"
        r = await c.post(f"{API}/auth/verify", json={"phone": "+998901234567", "code": "000000"})
        assert r.status_code in (400, 200)  # 200 only if the random code happened to be 000000
        assert (await c.post(f"{API}/auth/request-code", json={"phone": "901234567"})).status_code == 429
        assert (await c.post(f"{API}/auth/request-code", json={"phone": "12345"})).status_code == 400


async def test_barber_sees_only_own_bookings(client):
    jasur_booking = await book_as_client(client, 11, master_id=1)
    bekzod_booking = await book_as_client(client, 12, master_id=2)
    # A client session is not a staff session.
    assert (await client.get(f"{API}/staff/bookings", params=day_params())).status_code == 401

    async with make_client() as jasur:
        me = await staff_login(jasur, "jasur", "barber123")
        assert me["role"] == "barber" and me["master_id"] == 1 and me["salon"]["slug"] == "premium"
        day = (await jasur.get(f"{API}/staff/bookings", params=day_params())).json()
        assert [b["id"] for b in day] == [jasur_booking]
        assert (await jasur.patch(f"{API}/staff/bookings/{bekzod_booking}", json={"status": "no_show"})).status_code == 403
        r = await jasur.patch(f"{API}/staff/bookings/{jasur_booking}", json={"status": "completed"})
        assert r.status_code == 422  # tomorrow's visit can't be "done" yet: that would free the slot
        await move_to_past(jasur_booking)
        r = await jasur.patch(f"{API}/staff/bookings/{jasur_booking}", json={"status": "completed"})
        assert r.status_code == 200 and r.json()["status"] == "completed"
        # Barbers cannot book for clients or manage the team.
        assert (await jasur.get(f"{API}/staff/team")).status_code == 403


async def test_admin_sees_all_and_books_for_client(client):
    booking_id = await book_as_client(client, 11, master_id=1)
    async with make_client() as admin:
        me = await staff_login(admin, "Malika", "admin123")  # login is case-insensitive
        assert me["role"] == "admin"
        day = (await admin.get(f"{API}/staff/bookings", params=day_params())).json()
        assert [b["id"] for b in day] == [booking_id] and day[0]["client_phone"] == "+998901111111"
        r = await admin.post(
            f"{API}/staff/bookings",
            json={
                "branch_id": 1, "master_id": 1, "service_ids": [1], "starts_at": local(tomorrow(), 11, 30).isoformat(),
                "client_phone": "+998935555555", "client_name": "Telefon mijoz",
            },
        )
        assert r.status_code == 201 and r.json()["source"] == "admin"
        await move_to_past(booking_id)
        r = await admin.patch(f"{API}/staff/bookings/{booking_id}", json={"status": "no_show"})
        assert r.status_code == 200 and r.json()["status"] == "no_show"


async def test_wrong_password_locks_after_five_tries():
    async with make_client() as c:
        for _ in range(5):
            r = await c.post(f"{API}/staff/login", json={"login": "jasur", "password": "wrong"})
            assert r.status_code == 401
        r = await c.post(f"{API}/staff/login", json={"login": "jasur", "password": "barber123"})
        assert r.status_code == 429  # locked even with the right password
        r = await c.post(f"{API}/staff/login", json={"login": "nobody", "password": "x"})
        assert r.status_code == 401


async def test_owner_adds_barber_who_appears_on_site():
    async with make_client() as owner:
        await staff_login(owner, "owner", "owner123")
        r = await owner.post(f"{API}/staff/team", json={"name": "Diyor", "login": "diyor", "password": "diyor-2026"})
        assert r.status_code == 201, r.text
        member = r.json()
        assert (await owner.post(f"{API}/staff/team", json={"name": "X", "login": "diyor", "password": "123456"})).status_code == 409

        masters = (await owner.get(f"{API}/branches/1/masters", params={"service_ids": "1"})).json()
        assert "Diyor" in [m["name"] for m in masters]

        async with make_client() as diyor:
            me = await staff_login(diyor, "diyor", "diyor-2026")
            assert me["master_id"] == member["master_id"]

        # Deactivating hides him from clients and blocks his login.
        r = await owner.patch(f"{API}/staff/team/{member['id']}", json={"is_active": False})
        assert r.status_code == 200
        masters = (await owner.get(f"{API}/branches/1/masters")).json()
        assert "Diyor" not in [m["name"] for m in masters]
        async with make_client() as diyor:
            r = await diyor.post(f"{API}/staff/login", json={"login": "diyor", "password": "diyor-2026"})
            assert r.status_code == 401


async def test_admin_cannot_touch_owner_or_create_admins():
    async with make_client() as admin:
        await staff_login(admin, "malika", "admin123")
        team = (await admin.get(f"{API}/staff/team")).json()
        owner_id = next(m["id"] for m in team if m["role"] == "owner")
        assert (await admin.patch(f"{API}/staff/team/{owner_id}", json={"password": "hacked1"})).status_code == 403
        r = await admin.post(f"{API}/staff/team", json={"name": "A", "login": "admin2", "password": "123456", "role": "admin"})
        assert r.status_code == 403


async def test_change_own_password():
    async with make_client() as c:
        await staff_login(c, "bekzod", "barber123")
        assert (await c.post(f"{API}/staff/password", json={"old_password": "nope", "new_password": "newpass1"})).status_code == 400
        assert (await c.post(f"{API}/staff/password", json={"old_password": "barber123", "new_password": "newpass1"})).status_code == 204
    async with make_client() as c:
        await staff_login(c, "bekzod", "newpass1")
