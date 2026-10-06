import asyncio
from datetime import time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Booking, Reminder, ScheduleException, User
from app.services import booking as svc
from app.services.availability import now_utc
from tests.conftest import API, local, login, make_client, tomorrow

HAIRCUT, BEARD, COMBO = 1, 2, 3


async def slots(client, services, master_id=None, day=None):
    params = {"branch_id": 1, "service_ids": ",".join(map(str, services)), "date": str(day or tomorrow())}
    if master_id:
        params["master_id"] = master_id
    r = await client.get(f"{API}/available-slots", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def starts(payload):
    return [s["start"] for s in payload["slots"]]


async def book(client, start, services=(HAIRCUT,), master_id=2, **headers):
    return await client.post(
        f"{API}/bookings",
        json={"branch_id": 1, "master_id": master_id, "service_ids": list(services), "starts_at": start.isoformat()},
        headers=headers,
    )


async def test_catalog(client):
    services = (await client.get(f"{API}/branches/1/services")).json()
    assert [s["duration_min"] for s in services if s["id"] == COMBO] == [50]
    masters = (await client.get(f"{API}/branches/1/masters", params={"service_ids": "1,2"})).json()
    top = next(m for m in masters if m["rank"] == "Top-master")
    assert top["price"] == int(120_000 * 1.2) + int(80_000 * 1.2) and top["duration_min"] == 50


async def test_booking_removes_slot_and_blocks_second_client(client):
    start = local(tomorrow(), 12)
    assert start.isoformat() in starts(await slots(client, [HAIRCUT], master_id=2))

    r = await book(client, start)
    assert r.status_code == 201, r.text
    assert r.json()["total_price"] == 120_000

    remaining = starts(await slots(client, [HAIRCUT], master_id=2))
    for hhmm in ((11, 45), (12, 0), (12, 15)):  # every 30-min start that would overlap 12:00-12:30
        assert local(tomorrow(), *hhmm).isoformat() not in remaining
    assert local(tomorrow(), 12, 30).isoformat() in remaining

    async with make_client() as other:
        await login(other, "+998902222222")
        r = await book(other, start)
        assert r.status_code == 409
        assert "band" in r.json()["detail"]


async def test_combined_services_need_the_whole_duration(client):
    # Master 2 is busy 12:30-13:00, so a 50-minute combo cannot start at 11:45.
    assert (await book(client, local(tomorrow(), 12, 30))).status_code == 201
    combo = starts(await slots(client, [HAIRCUT, BEARD], master_id=2))
    assert local(tomorrow(), 11, 45).isoformat() not in combo  # 11:45-12:35 overlaps
    assert local(tomorrow(), 12, 0).isoformat() not in combo
    assert local(tomorrow(), 11, 30).isoformat() in combo  # 11:30-12:20 fits before 12:30
    assert local(tomorrow(), 13, 0).isoformat() in combo
    assert (await book(client, local(tomorrow(), 11, 45), services=(HAIRCUT, BEARD))).status_code == 409


async def test_any_master_fills_each_master_once(client):
    start = local(tomorrow(), 15)
    ids = []
    for i in range(3):
        async with make_client() as c:
            await login(c, f"+99890333333{i}")
            r = await book(c, start, master_id=None)
            assert r.status_code == 201, r.text
            ids.append(r.json()["master_id"])
    assert sorted(ids) == [1, 2, 3]
    assert (await book(client, start, master_id=None)).status_code == 409
    assert start.isoformat() not in starts(await slots(client, [HAIRCUT]))


async def test_idempotency_key_returns_same_booking(client):
    start = local(tomorrow(), 16)
    a = await book(client, start, **{"Idempotency-Key": "abc-123"})
    b = await book(client, start, **{"Idempotency-Key": "abc-123"})
    assert a.status_code == 201 and b.status_code == 200  # the replay books nothing new
    assert a.json()["id"] == b.json()["id"]


async def test_same_key_sent_at_once_books_once_and_never_says_taken(client, session):
    start = local(tomorrow(), 17)
    replies = await asyncio.gather(*(book(client, start, **{"Idempotency-Key": "burst-1"}) for _ in range(5)))
    assert sorted(r.status_code for r in replies) == [200, 200, 200, 200, 201]
    assert len({r.json()["id"] for r in replies}) == 1
    assert await session.scalar(select(func.count()).select_from(Booking)) == 1


async def test_cancel_frees_slot_and_removes_reminders(client, session):
    start = local(tomorrow() + timedelta(days=1), 12)
    r = await book(client, start)
    booking_id = r.json()["id"]
    assert await session.scalar(select(func.count()).select_from(Reminder)) == 2

    r = await client.post(f"{API}/bookings/{booking_id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert await session.scalar(select(func.count()).select_from(Reminder)) == 0
    assert start.isoformat() in starts(await slots(client, [HAIRCUT], master_id=2, day=start.date()))

    mine = (await client.get(f"{API}/me/bookings")).json()
    assert [b["status"] for b in mine] == ["cancelled"]


async def test_day_off_and_break(client, session):
    day = tomorrow()
    session.add(ScheduleException(master_id=2, date=day, kind="break", start_time=time(13), end_time=time(14)))
    session.add(ScheduleException(master_id=3, date=day, kind="day_off"))
    await session.commit()

    m2 = starts(await slots(client, [HAIRCUT], master_id=2))
    assert local(day, 12, 45).isoformat() not in m2
    assert local(day, 13, 30).isoformat() not in m2
    assert local(day, 12, 30).isoformat() in m2 and local(day, 14).isoformat() in m2
    assert (await slots(client, [HAIRCUT], master_id=3))["slots"] == []


async def test_outside_hours_and_past_rejected(client):
    assert (await book(client, local(tomorrow(), 20, 45))).status_code == 409  # would end after 21:00
    assert (await book(client, local(tomorrow(), 12, 5))).status_code == 409  # not on the 15-min grid
    assert (await book(client, local(tomorrow(), 9))).status_code == 409


async def test_requires_login():
    async with make_client() as anon:
        assert (await book(anon, local(tomorrow(), 12))).status_code == 401
        assert (await anon.get(f"{API}/staff/bookings", params={"date": str(tomorrow())})).status_code == 401


async def test_database_rejects_overlap_even_without_app_checks(session):
    user = User(salon_id=1, phone="+998909999999")
    session.add(user)
    await session.flush()
    base = dict(salon_id=1, user_id=user.id, master_id=1, branch_id=1, total_price=1, status="confirmed")
    session.add(Booking(starts_at=local(tomorrow(), 12), ends_at=local(tomorrow(), 12, 30), **base))
    await session.commit()
    session.add(Booking(starts_at=local(tomorrow(), 12, 15), ends_at=local(tomorrow(), 12, 45), **base))
    with pytest.raises(IntegrityError) as exc:
        await session.commit()
    assert svc._is_sqlstate(exc.value, "23P01")
    await session.rollback()
    # Back-to-back is fine: ranges are half-open [start, end).
    session.add(Booking(starts_at=local(tomorrow(), 12, 30), ends_at=local(tomorrow(), 13), **base))
    await session.commit()


async def test_twenty_clients_race_for_one_slot(session):
    """20 clients press "Tasdiqlash" for the same master and time at once: exactly one wins."""
    users = [User(salon_id=1, phone=f"+9989100000{i:02d}") for i in range(20)]
    session.add_all(users)
    await session.commit()
    user_ids = [u.id for u in users]
    start = local(tomorrow(), 18)

    async def attempt(uid):
        from app.db import SessionLocal

        async with SessionLocal() as s:
            user = await s.get(User, uid)
            try:
                await svc.create_booking(
                    s, salon_id=1, user=user, branch_id=1, master_id=1, service_ids=[HAIRCUT], starts_at=start
                )
                return "ok"
            except svc.SlotTaken:
                return "taken"

    results = await asyncio.gather(*(attempt(uid) for uid in user_ids))
    assert results.count("ok") == 1 and results.count("taken") == 19
    count = await session.scalar(select(func.count()).select_from(Booking).where(Booking.master_id == 1))
    assert count == 1


async def test_due_reminder_is_sent_once(client, session):
    from app.notifications import send_due_reminders
    from app.sms import ConsoleSms

    r = await book(client, local(tomorrow() + timedelta(days=2), 12))
    reminder = await session.scalar(select(Reminder).where(Reminder.kind == "24h"))
    reminder.send_at = now_utc() - timedelta(minutes=1)  # pretend it's due now
    await session.commit()

    ConsoleSms.sent.clear()
    assert await send_due_reminders() == 1
    assert len(ConsoleSms.sent) == 1 and "Ertaga" in ConsoleSms.sent[0][1]
    assert await send_due_reminders() == 0
    assert r.status_code == 201
