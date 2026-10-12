"""Russian: clients and staff pick it in the bot or on the site; messages, buttons and API errors follow."""

import re
import string

from sqlalchemy import select

from app import i18n
from app.config import settings
from app.db import SessionLocal
from app.models import User
from app.telegram import get_tg
from tests.conftest import API, local, make_client, staff_login, tomorrow
from tests.test_demo_flow import OWNER_CHAT, link_staff, sign_init, to
from tests.test_telegram import connect_bot, hook, msg, salon_row

OLGA = {"id": 6161, "first_name": "Ольга", "language_code": "ru"}


def tap(c, salon, who: dict, data: str):
    return hook(c, salon, {"update_id": 7, "callback_query": {"id": "q", "from": who, "data": data,
                                                              "message": {"message_id": 4, "chat": {"id": who["id"]}}}})


def test_every_message_has_both_languages_with_the_same_blanks():
    fields = lambda s: {f for _, f, _, _ in string.Formatter().parse(s) if f}  # noqa: E731
    for key, (uz, ru) in i18n.M.items():
        assert uz and ru and fields(uz) == fields(ru), key
    assert i18n.norm("ru") == "ru" and i18n.norm("uk") == "ru" and i18n.norm("en") == "uz" and i18n.norm(None) == "uz"
    assert i18n.error("Eng kam buyurtma 50 000 so'm", "ru") == "Минимальный заказ 50 000 сум"
    assert i18n.error("Buyurtma holati: Tayyor. Bu amal endi mumkin emas.", "ru") == "Статус заказа: Готов. Это действие уже невозможно."
    assert i18n.error("Kod noto'g'ri", "uz") == "Kod noto'g'ri"


async def test_a_russian_client_subscribes_books_and_hears_in_russian(monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://bron.example.uz")
    await connect_bot()
    await link_staff("owner", "owner123", OWNER_CHAT)
    salon = await salon_row()
    async with make_client() as c:
        await hook(c, salon, msg("/start", chat=OLGA))
        assert "Выберите язык" in to(OLGA["id"])[-1]
        await tap(c, salon, OLGA, "lang:ru")
        hello = get_tg().sent()[-1]
        assert hello["text"].startswith("Здравствуйте!") and hello["reply_markup"]["keyboard"][0][0]["text"] == "📱 Отправить номер"

        await hook(c, salon, msg(contact={"phone_number": "998977776655", "first_name": "Ольга", "user_id": OLGA["id"]}, chat=OLGA))
        assert "Вы подписались" in to(OLGA["id"])[-2]
        buttons = get_tg().sent()[-1]["reply_markup"]["inline_keyboard"]
        assert buttons[0][0] == {"text": "📅 Записаться", "web_app": {"url": "https://bron.example.uz/premium?lang=ru"}}
        assert buttons[1][0]["callback_data"] == "lang:uz"
        menu = [p for _, m, p in get_tg().calls if m == "setChatMenuButton" and p.get("chat_id") == OLGA["id"]][-1]
        assert menu["menu_button"]["text"] == "Записаться"
        async with SessionLocal() as s:
            assert (await s.scalar(select(User).where(User.phone == "+998977776655"))).language == "ru"

        r = await c.post(f"{API}/auth/webapp", json={"init_data": sign_init(OLGA["id"])})
        assert r.status_code == 200 and r.json()["language"] == "ru"
        r = await c.post(f"{API}/bookings", json={"branch_id": 1, "master_id": 2, "service_ids": [1],
                                                  "starts_at": local(tomorrow(), 15).isoformat()})
        assert r.status_code == 201, r.text
    sent = [p for p in get_tg().sent() if p["chat_id"] == OLGA["id"]][-1]
    assert "Запись подтверждена" in sent["text"] and "Мастер: \u2068Bekzod" in sent["text"] and "сум" in sent["text"]
    assert sent["reply_markup"]["inline_keyboard"][0][0]["text"] == "❌ Отменить"
    assert "Yangi bron" in to(OWNER_CHAT)[-1]  # the owner still reads Uzbek

    # back to Uzbek with one tap: the next messages are Uzbek
    async with make_client() as c:
        await tap(c, salon, OLGA, "lang:uz")
        assert to(OLGA["id"])[-1].startswith("Assalomu alaykum")
    async with SessionLocal() as s:
        assert (await s.scalar(select(User).where(User.phone == "+998977776655"))).language == "uz"


async def test_staff_switch_to_russian_in_the_bot_and_in_the_panel(client):
    await connect_bot()
    await link_staff("owner", "owner123", OWNER_CHAT)
    salon = await salon_row()
    owner = {"id": OWNER_CHAT, "first_name": "O"}
    async with make_client() as c:
        await hook(c, salon, msg("🌐 Русский", chat=owner))
        reply = get_tg().sent()[-1]
        assert "Язык: русский" in reply["text"] and reply["reply_markup"]["keyboard"][0][0]["text"] == "📋 Сегодня"
        assert reply["reply_markup"]["keyboard"][-1][0]["text"] == "🌐 O'zbekcha"

    r = await client.post(f"{API}/bookings", json={"branch_id": 1, "master_id": 2, "service_ids": [1],
                                                    "starts_at": local(tomorrow(), 16).isoformat()})
    assert r.status_code == 201
    news = [p for p in get_tg().sent() if p["chat_id"] == OWNER_CHAT][-1]
    assert "Новая запись" in news["text"] and "Мастер: \u2068Bekzod" in news["text"] and "сум" in news["text"]
    assert re.search(r"понедельник|вторник|среда|четверг|пятница|суббота|воскресенье", news["text"])
    assert news["reply_markup"]["inline_keyboard"][0][0]["text"] == "❌ Отменить"

    async with make_client() as c:
        await hook(c, salon, msg("📋 Завтра", chat=owner))
        assert "Завтра" in to(OWNER_CHAT)[-1] and "16:00" in to(OWNER_CHAT)[-1]

        await staff_login(c, "owner", "owner123")
        me = (await c.get(f"{API}/staff/me")).json()
        assert me["language"] == "ru" and me["salon"]["term_ru"][0] == "Мастер"
        r = await c.put(f"{API}/staff/me/language", json={"language": "uz"})
        assert r.status_code == 200 and r.json()["language"] == "uz"
    assert to(OWNER_CHAT)[-1].startswith("✅ Til: O'zbekcha")
    assert get_tg().sent()[-1]["reply_markup"]["keyboard"][0][0]["text"] == "📋 Bugun"


async def test_pages_get_errors_in_russian_and_the_site_language_reaches_the_bot(client):
    async with make_client() as c:
        r = await c.post(f"{API}/staff/login", json={"login": "owner", "password": "wrong-one"}, headers={"X-Lang": "ru"})
        assert r.status_code == 401 and r.json()["detail"] == "Неверный логин или пароль"
        r = await c.post(f"{API}/staff/login", json={"login": "owner", "password": "wrong-one"})
        assert r.json()["detail"] == "Login yoki parol noto'g'ri"
        assert (await c.get("/api/s/yoq-bunday", headers={"X-Lang": "ru"})).json()["detail"] == "Бизнес не найден"

    r = await client.put(f"{API}/me/language", json={"language": "ru"})
    assert r.status_code == 200 and r.json()["language"] == "ru"
    assert (await client.put(f"{API}/me/language", json={"language": "en"})).status_code == 422

    await connect_bot()
    salon = await salon_row()
    async with make_client() as site:
        start = (await site.post(f"{API}/auth/telegram", headers={"X-Lang": "ru"})).json()
        stranger = {"id": 7171, "first_name": "Иван"}
        await hook(site, salon, msg(f"/start {start['token']}", chat=stranger))
        assert "Чтобы войти на сайт" in to(7171)[-1]
        assert get_tg().sent()[-1]["reply_markup"]["keyboard"][0][0]["text"] == "📱 Отправить номер"
