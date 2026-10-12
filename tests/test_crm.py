"""The team's CRM: a list imported from the maps, conversations with each business, the money goal."""

from tests.test_platform_team import P, boss, signed  # noqa: F401  (boss is a fixture)


async def test_import_skips_businesses_already_in_the_list_and_hands_them_to_a_seller(boss):  # noqa: F811
    owner = await signed("boss", "platform-pass")
    seller = (await owner.post(f"{P}/team", json={"login": "malika", "name": "Malika", "password": "sotuv-2026", "role": "sales"})).json()
    await owner.post(f"{P}/leads", json={"name": "Gulnoza Beauty", "kind": "beauty", "phone": "+998 90 111 22 33"})

    rows = [
        {"name": "gulnoza  beauty", "kind": "beauty"},  # the same business, typed differently
        {"name": "Boshqa nom", "kind": "beauty", "phone": "901112233"},  # the same phone
        {"name": "Lola Kosmetologiya", "kind": "cosmetology", "district": "Yunusobod", "size": "small", "source": "2GIS"},
        {"name": "Lola Kosmetologiya", "kind": "cosmetology"},  # twice in the file
        {"name": "Nafis Studio", "kind": "beauty", "district": "Chilonzor", "staff_count": 6, "status": "paid"},
    ]
    r = await owner.post(f"{P}/leads/import", json={"leads": rows, "assigned_to": seller["id"]})
    assert r.status_code == 200 and r.json() == {"created": 2, "skipped": 3}

    leads = {x["name"]: x for x in (await owner.get(f"{P}/leads")).json()}
    assert leads["Lola Kosmetologiya"]["district"] == "Yunusobod" and leads["Lola Kosmetologiya"]["source"] == "2GIS"
    assert leads["Nafis Studio"]["status"] == "new" and leads["Nafis Studio"]["staff_count"] == 6  # an import never marks money
    assert leads["Nafis Studio"]["assigned_name"] == "Malika"

    malika = await signed("malika", "sotuv-2026")
    mine = {x["name"] for x in (await malika.get(f"{P}/leads")).json()}
    assert mine == {"Lola Kosmetologiya", "Nafis Studio"}
    # a seller's import is always their own, whoever they name
    r = await malika.post(f"{P}/leads/import", json={"leads": [{"name": "Yangi Salon"}], "assigned_to": None})
    assert r.json()["created"] == 1
    assert "Yangi Salon" in {x["name"] for x in (await malika.get(f"{P}/leads")).json()}


async def test_conversations_move_a_new_lead_on_and_only_its_people_see_them(boss):  # noqa: F811
    owner = await signed("boss", "platform-pass")
    await owner.post(f"{P}/team", json={"login": "bobur", "name": "Bobur", "password": "sotuv-2027", "role": "sales"})
    lead = (await owner.post(f"{P}/leads", json={"name": "Dilnoza Spa", "kind": "spa"})).json()

    r = await owner.post(f"{P}/leads/{lead['id']}/notes", json={"kind": "call", "text": "Egasi Dilnoza opa, daftarda yozadi. Juma kuni demo."})
    assert r.status_code == 201 and r.json()["user_name"] == "Ega"
    await owner.post(f"{P}/leads/{lead['id']}/notes", json={"kind": "demo", "text": "Demo yoqdi, narxni so'radi", "day": "2026-01-02"})
    notes = (await owner.get(f"{P}/leads/{lead['id']}/notes")).json()
    assert [n["kind"] for n in notes] == ["call", "demo"]  # newest day first

    out = [x for x in (await owner.get(f"{P}/leads")).json() if x["id"] == lead["id"]][0]
    assert out["status"] == "talked" and out["notes_count"] == 2

    r = await owner.patch(f"{P}/leads/{lead['id']}", json={"status": "lost", "lost_reason": "Qimmat dedi", "how_now": "Daftar",
                                                          "size": "medium", "staff_count": 4})
    assert r.json()["lost_reason"] == "Qimmat dedi" and r.json()["size"] == "medium"
    r = await owner.patch(f"{P}/leads/{lead['id']}", json={"status": "later", "clear_staff_count": True})
    assert r.json()["status"] == "later" and r.json()["staff_count"] is None

    bobur = await signed("bobur", "sotuv-2027")
    assert (await bobur.get(f"{P}/leads/{lead['id']}/notes")).status_code == 404
    assert (await bobur.post(f"{P}/leads/{lead['id']}/notes", json={"text": "x"})).status_code == 404


async def test_the_money_goal_is_for_the_owner_and_partner(boss):  # noqa: F811
    owner = await signed("boss", "platform-pass")
    assert (await owner.get(f"{P}/stats")).json()["goal"] == 0
    assert (await owner.put(f"{P}/goal", json={"goal": 21_600_000})).status_code == 200
    assert (await owner.get(f"{P}/stats")).json()["goal"] == 21_600_000
    await owner.post(f"{P}/team", json={"login": "nodir", "name": "Nodir", "password": "sotuv-2028", "role": "sales"})
    seller = await signed("nodir", "sotuv-2028")
    assert "goal" not in (await seller.get(f"{P}/stats")).json()
    assert (await seller.put(f"{P}/goal", json={"goal": 1})).status_code == 403
