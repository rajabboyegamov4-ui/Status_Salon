"""Which salon a request belongs to, and whether its subscription is active."""

import re
from datetime import date
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Salon
from app.services.availability import now_utc

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")
RESERVED_SLUGS = {"api", "platform", "docs", "openapi", "static", "admin", "panel", "www", "app", "health", "media"}
SUSPENDED_MESSAGE = "Salon onlayn bron xizmati vaqtincha to'xtatilgan. Iltimos, salonga qo'ng'iroq qiling."


def salon_today(salon: Salon | None = None) -> date:
    return now_utc().astimezone(ZoneInfo(settings.default_timezone)).date()


def subscription_active(salon: Salon) -> bool:
    return salon.is_active and salon.paid_until >= salon_today(salon)


async def get_salon(slug: str, session: AsyncSession = Depends(get_session)) -> Salon:
    """Path dependency for /api/s/{slug}/...: the salon must exist."""
    salon = await session.scalar(select(Salon).where(Salon.slug == slug.lower()))
    if salon is None:
        raise HTTPException(404, "Salon topilmadi")
    return salon


async def active_salon(salon: Salon = Depends(get_salon)) -> Salon:
    """Same, but refuses when the subscription has lapsed or the platform suspended the salon."""
    if not subscription_active(salon):
        raise HTTPException(402, SUSPENDED_MESSAGE)
    return salon


def validate_slug(slug: str) -> str:
    slug = slug.strip().lower()
    if not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
        raise HTTPException(422, "Manzil 3-40 ta lotin harf, raqam yoki '-' bo'lishi kerak")
    return slug
