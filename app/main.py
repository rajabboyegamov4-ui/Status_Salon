import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.exc import DBAPIError

from app.config import settings
from app.notifications import send_due_reminders
from app.routers import platform, profile, public, schedule, staff, telegram
from app.tenancy import RESERVED_SLUGS, SLUG_RE

logging.basicConfig(level=logging.INFO, stream=sys.stdout)  # Railway marks stderr lines as errors
# httpx logs every request URL at INFO, and a Telegram URL carries the salon's bot token.
for _name in ("httpx", "httpcore", "apscheduler"):  # apscheduler: one line per minute otherwise
    logging.getLogger(_name).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    if settings.run_scheduler:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler()
        scheduler.add_job(send_due_reminders, "interval", minutes=1, max_instances=1, coalesce=True)
        scheduler.start()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


_prod = settings.app_env == "production"
app = FastAPI(title="Salon bron tizimi", lifespan=lifespan,
              docs_url=None if _prod else "/docs", redoc_url=None, openapi_url=None if _prod else "/openapi.json")

@app.exception_handler(DBAPIError)
async def bad_value(request, exc: DBAPIError):
    # e.g. an id far beyond any real row: the database rejects the value, the caller gets a plain 422
    if isinstance(exc.orig.__cause__, asyncpg.exceptions.DataError):
        return JSONResponse({"detail": "Noto'g'ri qiymat"}, status_code=422)
    raise exc


for r in (platform.router, staff.router, schedule.router, profile.router, telegram.router, public.router):
    app.include_router(r, prefix="/api")
app.include_router(profile.media)


@app.get("/api/health")
async def health():
    return {"ok": True}


# The customer site and the barber/admin panel are single pages in ./web, served by this same service.
WEB = Path(__file__).resolve().parent.parent / "web"


def _page(name: str) -> str:
    body = (WEB / name).read_text(encoding="utf-8")
    return f'<!doctype html><html lang="uz"><head><meta charset="utf-8">{body}</html>'


# Every salon has its own addresses: /{slug} (clients), /{slug}/panel (barbers), /{slug}/admin (owner/admin).
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home():
    return _page("home.html")


@app.get("/platform", response_class=HTMLResponse, include_in_schema=False)
async def platform_page():
    return _page("platform.html")


def _salon_page(slug: str, name: str) -> str:
    if not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
        raise HTTPException(404)
    return _page(name)


@app.get("/{slug}", response_class=HTMLResponse, include_in_schema=False)
async def salon_site(slug: str):
    return _salon_page(slug, "index.html")


@app.get("/{slug}/panel", response_class=HTMLResponse, include_in_schema=False)
async def salon_panel(slug: str):
    return _salon_page(slug, "panel.html")


@app.get("/{slug}/admin", response_class=HTMLResponse, include_in_schema=False)
async def salon_admin(slug: str):
    return _salon_page(slug, "admin.html")
