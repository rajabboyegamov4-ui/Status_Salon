import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/salon_test")
os.environ["DB_NULL_POOL"] = "true"
os.environ["RUN_SCHEDULER"] = "false"
os.environ["APP_ENV"] = "development"
os.environ["SMS_BACKEND"] = "console"
os.environ["JWT_SECRET"] = "test-secret-that-is-at-least-32-bytes-long"

from datetime import datetime, time, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.availability import now_utc  # noqa: E402
from app.auth import PLATFORM_FAILS  # noqa: E402
from app.routers.public import OTP_PER_IP  # noqa: E402
from app.routers.telegram import LOGIN_PER_IP  # noqa: E402
from app.telegram import get_tg  # noqa: E402
from app.sms import ConsoleSms  # noqa: E402
from scripts.seed import seed_salon  # noqa: E402

TZ = ZoneInfo("Asia/Tashkent")
API = "/api/s/premium"


@pytest.fixture(scope="session", autouse=True)
async def schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture(autouse=True)
async def data(schema):
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    ConsoleSms.sent.clear()
    OTP_PER_IP.events.clear()
    LOGIN_PER_IP.events.clear()
    get_tg().calls.clear()
    PLATFORM_FAILS.events.clear()
    async with SessionLocal() as session:
        await seed_salon(session)
    yield


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


def tomorrow():
    return now_utc().astimezone(TZ).date() + timedelta(days=1)


def local(day, hh, mm=0):
    return datetime.combine(day, time(hh, mm), tzinfo=TZ)


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def login(client: httpx.AsyncClient, phone: str, name: str = "Mijoz", api: str = API) -> dict:
    r = await client.post(f"{api}/auth/request-code", json={"phone": phone})
    assert r.status_code == 204, r.text
    code = ConsoleSms.sent[-1][1].split("kodi ")[1][:6]
    r = await client.post(f"{api}/auth/verify", json={"phone": phone, "code": code, "name": name})
    assert r.status_code == 200, r.text
    return r.json()


async def staff_login(client: httpx.AsyncClient, login: str, password: str, api: str = API) -> dict:
    r = await client.post(f"{api}/staff/login", json={"login": login, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
async def client():
    async with make_client() as c:
        await login(c, "+998901111111")
        yield c
