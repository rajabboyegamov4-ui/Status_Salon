"""Day-one panel tools: weekly hours, days off and breaks, services and prices, walk-ins, moving a booking."""

from datetime import datetime

from tests.conftest import API, TZ, local, make_client, staff_login, tomorrow
from tests.test_staff import book_as_client


def slots_of(r):
    return [datetime.fromisoformat(s["start"]).astimezone(TZ) for s in r.json()["slots"]]


async def free_slots(c, master_id, day=None, path="/available-slots"):
    r = await c.get(f"{API}{path}", params={"branch_id": 1, "service_ids": "1", "date": str(day or tomorrow()), "master_id": master_id})
    assert r.status_code == 200, r.text
    return slots_of(r)


async def test_owner_sets_weekly_hours_and_the_site_follows():
    day = tomorrow()
    async with make_client() as owner:
        await staff_login(owner, "owner", "owner123")
        days = [{"weekday": d, "start": "12:00", "end": "15:00"} for d in range(7) if d != day.weekday()]
        r = await owner.put(f"{API}/staff/masters/2/hours", json={"days": days + [{"weekday": day.weekday(), "start": "12:00", "end": "14:00"}]})
        assert r.status_code == 200, r.text
        assert len(r.json()["days"]) == 7
        slots = await free_slots(owner, 2, day)
        assert slots and all(12 <= s.hour < 14 for s in slots)
        bad = await owner.put(f"{API}/staff/masters/2/hours", json={"days": [{"weekday": 0, "start": "15:00", "end": "12:00"}]})
        assert bad.status_code == 422
        # a weekday left out is a day off
        await owner.put(f"{API}/staff/masters/2/hours", json={"days": days})
        assert await free_slots(owner, 2, day) == []
    async with make_client() as barber:
        await staff_login(barber, "jasur", "barber123")
        assert (await barber.put(f"{API}/staff/masters/1/hours", json={"days": []})).status_code == 403
        assert (await barber.get(f"{API}/staff/masters/2/hours")).status_code == 403
        assert (await barber.get(f"{API}/staff/masters/1/hours")).status_code == 200


async def test_barber_takes_a_day_off_and_a_break_and_sees_conflicts(client):
    day = tomorrow()
    await book_as_client(client, 14, master_id=1)
    async with make_client() as barber:
        me = await staff_login(barber, "jasur", "barber123")
        assert me["branch_id"] == 1
        r = await barber.post(f"{API}/staff/exceptions", json={"master_id": 1, "date": str(day), "kind": "break", "start": "13:30", "end": "15:00"})
        assert r.status_code == 201 and r.json()["conflicts"] == 1, r.text
        slots = await free_slots(barber, 1, day)
        assert slots and not any(local(day, 13, 30) <= s < local(day, 15) for s in slots)
        assert (await barber.post(f"{API}/staff/exceptions", json={"master_id": 2, "date": str(day), "kind": "day_off"})).status_code == 403
        off = await barber.post(f"{API}/staff/exceptions", json={"master_id": 1, "date": str(day), "kind": "day_off"})
        assert off.status_code == 201
        assert await free_slots(barber, 1, day) == []
        hours = (await barber.get(f"{API}/staff/day-hours", params={"branch_id": 1, "date": str(day)})).json()
        assert hours == [{"master_id": 1, "intervals": []}]
        listed = (await barber.get(f"{API}/staff/exceptions", params={"from": str(day), "to": str(day)})).json()
        assert {e["kind"] for e in listed} == {"break", "day_off"}
        assert (await barber.delete(f"{API}/staff/exceptions/{off.json()['id']}")).status_code == 204
        assert await free_slots(barber, 1, day)


async def test_owner_edits_services_prices_and_who_does_them():
    async with make_client() as owner:
        await staff_login(owner, "owner", "owner123")
        r = await owner.post(f"{API}/staff/services", params={"branch_id": 1},
                             json={"name": "Bolalar soch", "price": 70000, "duration_min": 20, "masters": [{"master_id": 2}, {"master_id": 3, "price": 90000}]})
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        public = (await owner.get(f"{API}/masters/3/services")).json()
        assert any(s["id"] == sid and s["price"] == 90000 for s in public), public
        r = await owner.patch(f"{API}/staff/services/{sid}", params={"branch_id": 1}, json={"price": 75000, "masters": [{"master_id": 2}]})
        assert r.json()["price"] == 75000 and [m["master_id"] for m in r.json()["masters"]] == [2]
        assert not any(s["id"] == sid for s in (await owner.get(f"{API}/masters/3/services")).json())
        r = await owner.patch(f"{API}/staff/services/{sid}", params={"branch_id": 1}, json={"is_active": False})
        assert r.json()["is_active"] is False
        assert not any(s["id"] == sid for s in (await owner.get(f"{API}/masters/2/services")).json())
    async with make_client() as barber:
        await staff_login(barber, "jasur", "barber123")
        assert (await barber.post(f"{API}/staff/services", params={"branch_id": 1}, json={"name": "x", "price": 1, "duration_min": 10})).status_code == 403
        assert (await barber.get(f"{API}/staff/services", params={"branch_id": 1})).status_code == 200


async def test_barber_seats_a_walk_in_without_a_phone_into_own_chair_only():
    async with make_client() as barber:
        await staff_login(barber, "jasur", "barber123")
        slots = await free_slots(barber, 1, path="/staff/available-slots")
        assert slots
        body = {"branch_id": 1, "service_ids": [1], "starts_at": slots[0].isoformat(), "client_name": "Ali"}
        r = await barber.post(f"{API}/staff/bookings", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["master_id"] == 1 and "Ali" in r.json()["comment"]
        r = await barber.post(f"{API}/staff/bookings", json={**body, "master_id": 2, "starts_at": slots[-1].isoformat()})
        assert r.status_code == 403


async def test_staff_move_a_booking_and_the_old_time_frees_up(client):
    booking_id = await book_as_client(client, 12, master_id=1)
    day = tomorrow()
    async with make_client() as owner:
        await staff_login(owner, "owner", "owner123")
        r = await owner.post(f"{API}/staff/bookings/{booking_id}/move", json={"starts_at": local(day, 16).isoformat(), "master_id": 2})
        assert r.status_code == 200, r.text
        assert r.json()["master_id"] == 2 and datetime.fromisoformat(r.json()["starts_at"]) == local(day, 16)
        assert local(day, 12) in await free_slots(owner, 1, day)  # the old time is free again
        other = await book_as_client(client, 18, master_id=2)
        r = await owner.post(f"{API}/staff/bookings/{other}/move", json={"starts_at": local(day, 16).isoformat()})
        assert r.status_code == 409
    async with make_client() as barber:
        await staff_login(barber, "jasur", "barber123")
        r = await barber.post(f"{API}/staff/bookings/{booking_id}/move", json={"starts_at": local(day, 17).isoformat()})
        assert r.status_code == 403  # now Bekzod's
