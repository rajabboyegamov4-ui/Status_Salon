"""The platform owner's panel: connect salons, extend or stop their monthly subscription.

/api/platform/...  The login and password hash come from PLATFORM_LOGIN / PLATFORM_PASSWORD_HASH.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth
from app.db import get_session
from app.models import Booking, Salon, Staff
from app.ratelimit import client_ip
from app.schemas import PlatformLogin, PlatformSalonOut, SalonCreate, SalonUpdate
from app.services import team
from app.services.availability import now_utc
from app.tenancy import salon_today, subscription_active, validate_slug

router = APIRouter(prefix="/platform", tags=["platform"])


def add_months(d: date, months: int) -> date:
    """Same day N months later (the 31st becomes the month's last day)."""
    m = d.month - 1 + months
    year, month = d.year + m // 12, m % 12 + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError


async def _out(session: AsyncSession, salon: Salon) -> PlatformSalonOut:
    staff_count = await session.scalar(select(func.count()).where(Staff.salon_id == salon.id, Staff.is_active))
    bookings = await session.scalar(
        select(func.count()).where(Booking.salon_id == salon.id, Booking.created_at > now_utc() - timedelta(days=30))
    )
    return PlatformSalonOut(
        id=salon.id, slug=salon.slug, name=salon.name, phone=salon.phone, plan=salon.plan, kind=salon.kind,
        paid_until=salon.paid_until, is_active=salon.is_active, active=subscription_active(salon),
        created_at=salon.created_at, staff_count=staff_count or 0, bookings_30d=bookings or 0,
    )


@router.post("/login", status_code=204)
async def login(body: PlatformLogin, request: Request):
    try:
        token = auth.platform_login(body.login, body.password, client_ip(request))
    except auth.AuthError as e:
        raise HTTPException(e.status, str(e))
    response = Response(status_code=204)
    auth.set_cookie(response, auth.PLATFORM_COOKIE, token, auth.cookie_path(None), days=1)
    return response


@router.post("/logout", status_code=204)
async def logout():
    response = Response(status_code=204)
    auth.clear_cookie(response, auth.PLATFORM_COOKIE, auth.cookie_path(None))
    return response


@router.get("/me", status_code=204, dependencies=[Depends(auth.platform_admin)])
async def me():
    return Response(status_code=204)


@router.get("/salons", response_model=list[PlatformSalonOut], dependencies=[Depends(auth.platform_admin)])
async def salons(session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Salon).order_by(Salon.paid_until, Salon.id))).all()
    return [await _out(session, s) for s in rows]


@router.post("/salons", response_model=PlatformSalonOut, status_code=201, dependencies=[Depends(auth.platform_admin)])
async def create_salon(body: SalonCreate, session: AsyncSession = Depends(get_session)):
    slug = validate_slug(body.slug)
    try:
        salon = await team.create_salon(
            session, slug=slug, name=body.name.strip(), phone=body.phone.strip(),
            paid_until=add_months(salon_today(), body.months),
            branch_name=body.branch_name.strip(), branch_address=body.branch_address.strip(),
            owner_name=body.owner_name.strip(), owner_login=body.owner_login, owner_password=body.owner_password,
            kind=body.kind, sample_masters=body.sample_masters,
        )
    except team.TeamError as e:
        raise HTTPException(409, str(e))
    await session.commit()
    await session.refresh(salon)
    return await _out(session, salon)


@router.patch("/salons/{salon_id}", response_model=PlatformSalonOut, dependencies=[Depends(auth.platform_admin)])
async def update_salon(salon_id: int, body: SalonUpdate, session: AsyncSession = Depends(get_session)):
    salon = await session.get(Salon, salon_id, with_for_update=True)  # two payments at once both count
    if salon is None:
        raise HTTPException(404, "Salon topilmadi")
    if body.name is not None:
        salon.name = body.name.strip()
    if body.phone is not None:
        salon.phone = body.phone.strip()
    if body.paid_until is not None:
        salon.paid_until = body.paid_until
    if body.extend_months:
        # A payment extends from the current end date, or from today if it already lapsed.
        salon.paid_until = add_months(max(salon.paid_until, salon_today()), body.extend_months)
    if body.is_active is not None:
        salon.is_active = body.is_active
    await session.commit()
    await session.refresh(salon)
    return await _out(session, salon)
