"""Barber and salon profiles: photos, experience, works, location, client reviews."""

from datetime import timedelta

from sqlalchemy import update

from app.models import Booking
from tests.conftest import API, local, login, make_client, staff_login, tomorrow

# Smallest valid images of each accepted type.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def upload(c, kind, body, ref_id=None, content_type="image/jpeg"):
    params = {"kind": kind}
    if ref_id is not None:
        params["ref_id"] = ref_id
    return await c.post(f"{API}/staff/photos", params=params, content=body, headers={"Content-Type": content_type})


async def test_public_profile_has_experience_and_location():
    async with make_client() as c:
        masters = (await c.get(f"{API}/branches/1/masters")).json()
        jasur = next(m for m in masters if m["name"] == "Jasur")
        assert jasur["experience_years"] == 9 and "Fade" in jasur["specialties"] and jasur["bio"]
        assert jasur["rating"] is None and jasur["reviews_count"] == 0
        branch = (await c.get(f"{API}/branches")).json()[0]
        assert branch["lat"] and branch["lng"] and "metro" in branch["landmark"]
        info = (await c.get(API)).json()
        assert info["instagram"] == "premiumbarber.uz"


async def test_barber_uploads_face_and_works_only_for_self():
    async with make_client() as jasur:
        await staff_login(jasur, "jasur", "barber123")
        r = await upload(jasur, "master", JPEG)  # ref_id defaults to own master
        assert r.status_code == 201, r.text
        face = r.json()["url"]
        media = await jasur.get(face)
        assert media.status_code == 200 and media.headers["content-type"] == "image/jpeg" and media.content == JPEG

        r = await upload(jasur, "work", PNG, content_type="image/png")
        assert r.status_code == 201
        work_id = r.json()["id"]
        assert (await upload(jasur, "work", JPEG, ref_id=2)).status_code == 403  # Bekzod's profile
        assert (await upload(jasur, "cover", JPEG)).status_code == 403
        assert (await upload(jasur, "work", b"<svg></svg>")).status_code == 422  # not an image

        r = await jasur.patch(f"{API}/staff/masters/1", json={"bio": "Yangi bio", "experience_years": 10, "specialties": ["Fade", "Soqol"]})
        assert r.status_code == 200 and r.json()["specialties"] == ["Fade", "Soqol"]
        assert (await jasur.patch(f"{API}/staff/masters/1", json={"rank": "Boss"})).status_code == 403
        assert (await jasur.patch(f"{API}/staff/masters/2", json={"bio": "x"})).status_code == 403

        # A new face replaces the old one.
        r2 = await upload(jasur, "master", PNG, content_type="image/png")
        assert (await jasur.get(face)).status_code == 404

        masters = (await jasur.get(f"{API}/branches/1/masters")).json()
        me = next(m for m in masters if m["id"] == 1)
        assert me["photo_url"] == r2.json()["url"] and [w["id"] for w in me["works"]] == [work_id]
        assert me["experience_years"] == 10

        assert (await jasur.delete(f"/api/s/premium/staff/photos/{work_id}")).status_code == 204
        masters = (await jasur.get(f"{API}/branches/1/masters")).json()
        assert next(m for m in masters if m["id"] == 1)["works"] == []


async def test_admin_sets_cover_branch_photos_and_location():
    async with make_client() as admin:
        await staff_login(admin, "malika", "admin123")
        cover = (await upload(admin, "cover", JPEG)).json()["url"]
        assert (await admin.get(API)).json()["cover_url"] == cover
        r = await upload(admin, "branch", JPEG, ref_id=1)
        assert r.status_code == 201
        r = await admin.patch(f"{API}/staff/branches/1", json={"lat": 41.3, "lng": 69.25, "landmark": "Metro yonida"})
        assert r.status_code == 200 and r.json()["lat"] == 41.3 and len(r.json()["photos"]) == 1
        assert (await admin.patch(f"{API}/staff/branches/1", json={"lat": 41.3})).status_code == 422
        r = await admin.patch(f"{API}/staff/salon", json={"description": "Eng yaxshi salon"})
        assert r.json()["description"] == "Eng yaxshi salon"
        # Another salon's branch is out of reach.
        assert (await admin.patch(f"{API}/staff/branches/999", json={"landmark": "x"})).status_code == 404


async def test_client_reviews_completed_visit_once(client, session):
    r = await client.post(
        f"{API}/bookings",
        json={"branch_id": 1, "master_id": 1, "service_ids": [1], "starts_at": local(tomorrow(), 11).isoformat()},
    )
    booking_id = r.json()["id"]
    review = {"rating": 5, "comment": "Zo'r fade, rahmat!"}
    # Not visited yet.
    assert (await client.post(f"{API}/bookings/{booking_id}/review", json=review)).status_code == 422

    await session.execute(update(Booking).where(Booking.id == booking_id).values(status="completed"))
    await session.commit()
    r = await client.post(f"{API}/bookings/{booking_id}/review", json=review)
    assert r.status_code == 201, r.text
    assert r.json()["client_name"] == "Mijoz" and r.json()["services"] == "Soch olish"
    assert (await client.post(f"{API}/bookings/{booking_id}/review", json=review)).status_code == 422
    assert (await client.post(f"{API}/bookings/{booking_id}/review", json={"rating": 6})).status_code == 422
    mine = (await client.get(f"{API}/me/bookings")).json()
    assert mine[0]["review_rating"] == 5

    # Someone else cannot review my visit.
    async with make_client() as other:
        await login(other, "+998905550000", "Aziz Karimov")
        assert (await other.post(f"{API}/bookings/{booking_id}/review", json=review)).status_code == 404

    masters = (await client.get(f"{API}/branches/1/masters")).json()
    jasur = next(m for m in masters if m["id"] == 1)
    assert jasur["rating"] == 5.0 and jasur["reviews_count"] == 1
    reviews = (await client.get(f"{API}/masters/1/reviews")).json()
    assert [x["comment"] for x in reviews] == ["Zo'r fade, rahmat!"]

    # The barber sees it; only a manager can hide it, and hidden reviews leave the rating.
    async with make_client() as jasur_c:
        await staff_login(jasur_c, "jasur", "barber123")
        assert len((await jasur_c.get(f"{API}/staff/reviews")).json()) == 1
        review_id = reviews[0]["id"]
        assert (await jasur_c.patch(f"{API}/staff/reviews/{review_id}", json={"is_hidden": True})).status_code == 403
    async with make_client() as admin:
        await staff_login(admin, "malika", "admin123")
        assert (await admin.patch(f"{API}/staff/reviews/{review_id}", json={"is_hidden": True})).status_code == 200
    assert (await client.get(f"{API}/masters/1/reviews")).json() == []
    masters = (await client.get(f"{API}/branches/1/masters")).json()
    assert next(m for m in masters if m["id"] == 1)["reviews_count"] == 0


async def test_front_page_lists_barbers_best_first_with_their_own_prices():
    async with make_client() as c:
        masters = (await c.get(f"{API}/masters")).json()
        assert [m["name"] for m in masters] == ["Jasur", "Bekzod", "Sardor"]  # top barber, then by experience
        assert all(m["branch_name"] for m in masters) and "phone" not in masters[0]

        jasur = (await c.get(f"{API}/masters/{masters[0]['id']}/services")).json()
        sardor = (await c.get(f"{API}/masters/{masters[2]['id']}/services")).json()
        assert jasur and {s["id"] for s in jasur} <= {s["id"] for s in (await c.get(f"{API}/branches/1/services")).json()}
        haircut = lambda rows: next(s["price"] for s in rows if s["name"] == "Soch olish")  # noqa: E731
        assert haircut(jasur) > haircut(sardor)  # Top-master costs more

        assert (await c.get(f"{API}/masters/999/services")).status_code == 404
        assert (await c.get("/api/s/nope/masters")).status_code == 404
