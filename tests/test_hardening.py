"""Fixes from the full review: status rules, sessions after a password change, odd input, production config."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services.availability import now_utc
from tests.conftest import API, TZ, make_client, staff_login, tomorrow
from tests.test_staff import book_as_client, move_to_past


async def test_cancelled_visit_cannot_be_marked_done_or_reviewed(client):
    booking_id = await book_as_client(client, 13, master_id=1)
    assert (await client.post(f"{API}/bookings/{booking_id}/cancel")).status_code == 200
    async with make_client() as owner:
        await staff_login(owner, "owner", "owner123")
        r = await owner.patch(f"{API}/staff/bookings/{booking_id}", json={"status": "completed"})
        assert r.status_code == 422
    r = await client.post(f"{API}/bookings/{booking_id}/review", json={"rating": 5, "comment": ""})
    assert r.status_code == 422


async def test_wrong_tap_can_be_fixed_until_the_client_reviews(client):
    booking_id = await book_as_client(client, 14, master_id=1)
    await move_to_past(booking_id)
    async with make_client() as jasur:
        await staff_login(jasur, "jasur", "barber123")
        set_to = lambda st: jasur.patch(f"{API}/staff/bookings/{booking_id}", json={"status": st})  # noqa: E731
        assert (await set_to("no_show")).status_code == 200
        assert (await set_to("completed")).status_code == 200
        assert (await client.post(f"{API}/bookings/{booking_id}/review", json={"rating": 5, "comment": "Zo'r"})).status_code == 201
        assert (await set_to("no_show")).status_code == 422


async def test_password_change_signs_out_other_devices():
    async with make_client() as phone, make_client() as laptop:
        await staff_login(phone, "jasur", "barber123")
        await staff_login(laptop, "jasur", "barber123")
        r = await phone.post(f"{API}/staff/password", json={"old_password": "barber123", "new_password": "yangi-parol-1"})
        assert r.status_code == 204
        assert (await phone.get(f"{API}/staff/me")).status_code == 200  # this device stays in
        assert (await laptop.get(f"{API}/staff/me")).status_code == 401

    async with make_client() as bekzod, make_client() as owner:
        await staff_login(bekzod, "bekzod", "barber123")
        await staff_login(owner, "owner", "owner123")
        team = (await owner.get(f"{API}/staff/team")).json()
        bid = next(m["id"] for m in team if m["login"] == "bekzod")
        assert (await owner.patch(f"{API}/staff/team/{bid}", json={"password": "reset-123"})).status_code == 200
        assert (await bekzod.get(f"{API}/staff/me")).status_code == 401


async def test_null_profile_field_is_ignored_not_a_crash():
    async with make_client() as jasur:
        await staff_login(jasur, "jasur", "barber123")
        r = await jasur.patch(f"{API}/staff/masters/1", json={"bio": None, "instagram": None})
        assert r.status_code == 200 and r.json()["bio"]


async def test_huge_ids_are_a_bad_request_not_a_crash():
    async with make_client() as c:
        assert (await c.get(f"{API}/branches/99999999999/services")).status_code == 422
        r = await c.get(f"{API}/available-slots", params={"branch_id": 1, "service_ids": "99999999999", "date": str(tomorrow())})
        assert r.status_code == 422


async def test_staff_can_seat_a_walk_in_now():
    async with make_client() as admin:
        await staff_login(admin, "malika", "admin123")
        soon = now_utc().astimezone(TZ) + timedelta(minutes=20)
        start = soon.replace(minute=(soon.minute // 15 + 1) * 15 % 60, second=0, microsecond=0)
        if start <= soon:
            start += timedelta(hours=1)
        if not 10 <= start.hour < 20:
            pytest.skip("outside working hours right now")
        r = await admin.post(f"{API}/staff/bookings", json={
            "branch_id": 1, "master_id": 2, "service_ids": [1], "starts_at": start.isoformat(),
            "client_phone": "+998935555556", "client_name": "Kelib qoldi",
        })
        assert r.status_code == 201, r.text


async def test_code_requests_in_one_salon_do_not_block_another(client):
    from app.db import SessionLocal
    from app.services.team import create_salon

    async with SessionLocal() as s:
        await create_salon(s, slug="boshqa", name="Boshqa", paid_until=tomorrow() + timedelta(days=30),
                           owner_name="O", owner_login="o", owner_password="owner-123")
        await s.commit()
    async with make_client() as c:
        assert (await c.post("/api/s/boshqa/auth/request-code", json={"phone": "+998907777777"})).status_code == 204
        assert (await c.post(f"{API}/auth/request-code", json={"phone": "+998907777777"})).status_code == 204


async def test_media_is_not_a_salon_address():
    async with make_client() as c:
        assert (await c.get("/media/panel")).status_code == 404
    from app.tenancy import RESERVED_SLUGS
    assert "media" in RESERVED_SLUGS


def test_production_refuses_weak_secret_and_forces_https_cookies():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", jwt_secret="change-me")
    live = dict(telegram_backend="live", public_url="https://bron.example.uz")
    s = Settings(_env_file=None, app_env="production", jwt_secret="x" * 40, cookie_secure=False, **live)
    assert s.cookie_secure is True
    # a production server whose bot would silently send nothing must not start
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", jwt_secret="x" * 40)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", jwt_secret="x" * 40, telegram_backend="live", public_url="http://a.uz")


async def test_code_login_is_closed_when_sms_is_off(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "sms_backend", "none")
    async with make_client() as c:
        assert (await c.post(f"{API}/auth/request-code", json={"phone": "+998901112233"})).status_code == 404
        assert (await c.post(f"{API}/auth/verify", json={"phone": "+998901112233", "code": "000000"})).status_code == 404
