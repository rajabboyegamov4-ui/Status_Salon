"""The customer site of one salon: /api/s/{slug}/..."""

from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth
from app.config import settings
from app.db import get_session
from app.models import Booking, Branch, BranchService, Master, MasterService, Salon, Service, User
from app.notifications import send_confirmation
from app.schemas import (
    BookingCreate,
    BookingOut,
    BranchOut,
    CodeRequest,
    CodeVerify,
    DateAvailability,
    MasterCardOut,
    MasterOut,
    MasterQuoteOut,
    ReviewCreate,
    ReviewOut,
    SalonInfo,
    ServiceOut,
    SlotOut,
    SlotsOut,
    UserOut,
)
from app.security import issue_token
from app.services import booking as svc
from app.services import profiles
from app.services.availability import NotOffered, branch_tz, eligible_masters, master_day_slots, now_utc, quote
from app.ratelimit import Window, client_ip
from app.sms import get_sms
from app.tenancy import active_salon, get_salon

router = APIRouter(prefix="/s/{slug}", tags=["salon"])

OTP_PER_IP = Window(20, 3600)
SLOT_TAKEN_MESSAGE = "Bu vaqt hozirgina band bo'ldi. Iltimos, boshqa vaqtni tanlang."


def parse_ids(raw: str) -> list[int]:
    try:
        ids = sorted({int(x) for x in raw.split(",") if x.strip()})
    except ValueError:
        raise HTTPException(422, "service_ids: vergul bilan ajratilgan sonlar bo'lishi kerak")
    if not ids:
        raise HTTPException(422, "Kamida bitta xizmat tanlang")
    return ids


async def salon_branch(session: AsyncSession, salon: Salon, branch_id: int) -> Branch:
    """The branch, only if it belongs to this salon."""
    branch = await session.get(Branch, branch_id)
    if branch is None or not branch.is_active or branch.salon_id != salon.id:
        raise HTTPException(404, "Filial topilmadi")
    return branch


# ---------- catalog ----------

@router.get("", response_model=SalonInfo)
async def salon_info(salon: Salon = Depends(get_salon)):
    return profiles.salon_info(salon)


@router.get("/branches", response_model=list[BranchOut])
async def branches(salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)):
    rows = (
        await session.scalars(select(Branch).where(Branch.salon_id == salon.id, Branch.is_active).order_by(Branch.id))
    ).all()
    photos = await profiles.photos_by_ref(session, salon.id, "branch", [b.id for b in rows])
    return [BranchOut.model_validate(b).model_copy(update={"photos": photos[b.id]}) for b in rows]


@router.get("/branches/{branch_id}/services", response_model=list[ServiceOut])
async def branch_services(branch_id: int, salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)):
    await salon_branch(session, salon, branch_id)
    rows = await session.execute(
        select(Service, BranchService.price, BranchService.duration_min)
        .join(BranchService, BranchService.service_id == Service.id)
        .where(BranchService.branch_id == branch_id, BranchService.is_active, Service.is_active, Service.salon_id == salon.id)
        .order_by(Service.category, Service.id)
    )
    return [
        ServiceOut(id=s.id, name=s.name, description=s.description, category=s.category, price=price, duration_min=dur)
        for s, price, dur in rows
    ]


@router.get("/branches/{branch_id}/masters", response_model=list[MasterQuoteOut])
async def branch_masters(
    branch_id: int,
    service_ids: str = Query("", description="1,2,3"),
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    await salon_branch(session, salon, branch_id)
    ids = parse_ids(service_ids) if service_ids else []
    masters = await eligible_masters(session, branch_id, ids)
    works = await profiles.photos_by_ref(session, salon.id, "work", [m.id for m in masters])
    stars = await profiles.ratings(session, [m.id for m in masters])
    out = []
    for m in masters:
        item = MasterQuoteOut.model_validate(m)
        item.works = works[m.id]
        item.rating, item.reviews_count = stars[m.id]
        if ids:
            q = await quote(session, branch_id, m.id, ids)
            item.price, item.duration_min = q.price, int(q.duration.total_seconds() // 60)
        out.append(item)
    return out


def front_page_order(m: MasterCardOut) -> tuple:
    """Top barbers first, then the most experienced, then the best rated."""
    return (
        "top" not in m.rank.lower(),
        -(m.experience_years if m.experience_years is not None else -1),
        -(m.rating or 0),
        m.id,
    )


@router.get("/masters", response_model=list[MasterCardOut])
async def all_masters(salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)):
    """Every working barber of the salon across its branches, best first."""
    rows = (
        await session.execute(
            select(Master, Branch.name)
            .join(Branch, Branch.id == Master.branch_id)
            .where(Branch.salon_id == salon.id, Branch.is_active, Master.is_active)
        )
    ).all()
    mids = [m.id for m, _ in rows]
    works = await profiles.photos_by_ref(session, salon.id, "work", mids)
    stars = await profiles.ratings(session, mids)
    out = []
    for m, branch_name in rows:
        item = MasterCardOut(**MasterOut.model_validate(m).model_dump(), branch_id=m.branch_id, branch_name=branch_name)
        item.works = works[m.id]
        item.rating, item.reviews_count = stars[m.id]
        out.append(item)
    return sorted(out, key=front_page_order)


async def salon_master(session: AsyncSession, salon: Salon, master_id: int) -> Master:
    master = await session.get(Master, master_id)
    branch = await session.get(Branch, master.branch_id) if master else None
    if master is None or not master.is_active or branch.salon_id != salon.id or not branch.is_active:
        raise HTTPException(404, "Barber topilmadi")
    return master


@router.get("/masters/{master_id}/services", response_model=list[ServiceOut])
async def master_services(master_id: int, salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)):
    """What this barber does, at this barber's own price and pace."""
    master = await salon_master(session, salon, master_id)
    rows = await session.execute(
        select(
            Service,
            func.coalesce(MasterService.price_override, BranchService.price),
            func.coalesce(MasterService.duration_override, BranchService.duration_min),
        )
        .join(BranchService, (BranchService.service_id == Service.id) & (BranchService.branch_id == master.branch_id))
        .join(MasterService, (MasterService.service_id == Service.id) & (MasterService.master_id == master.id))
        .where(BranchService.is_active, Service.is_active, Service.salon_id == salon.id)
        .order_by(Service.category, Service.id)
    )
    return [
        ServiceOut(id=s.id, name=s.name, description=s.description, category=s.category, price=int(price), duration_min=int(dur))
        for s, price, dur in rows
    ]


@router.get("/masters/{master_id}/reviews", response_model=list[ReviewOut])
async def master_reviews(
    master_id: int,
    limit: int = Query(30, ge=1, le=100),
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    master = await session.get(Master, master_id)
    branch = await session.get(Branch, master.branch_id) if master else None
    if master is None or branch.salon_id != salon.id:
        raise HTTPException(404, "Barber topilmadi")
    return await profiles.list_reviews(session, salon.id, master_id=master_id, limit=limit)


async def _slots(session, branch: Branch, ids: list[int], day: date, master_id: int | None, lead_min: int | None = None) -> SlotsOut:
    tz = branch_tz(branch)
    masters = await eligible_masters(session, branch.id, ids)
    if master_id is not None:
        masters = [m for m in masters if m.id == master_id]
        if not masters:
            raise HTTPException(404, "Bu master tanlangan xizmatlarni bajarmaydi")
    now = now_utc()
    by_start: dict = {}
    durations = set()
    for m in masters:
        try:
            q = await quote(session, branch.id, m.id, ids)
        except NotOffered:
            continue
        durations.add(int(q.duration.total_seconds() // 60))
        for start in await master_day_slots(session, m.id, tz, day, q.duration, now, lead_min=lead_min):
            by_start.setdefault(start, []).append(m.id)
    return SlotsOut(
        date=day,
        timezone=str(tz),
        duration_min=durations.pop() if len(durations) == 1 else None,
        slots=[SlotOut(start=s, master_ids=mids) for s, mids in sorted(by_start.items())],
    )


@router.get("/available-slots", response_model=SlotsOut)
async def available_slots(
    branch_id: int,
    service_ids: str = Query(..., description="1,2,3"),
    date: date = Query(..., description="YYYY-MM-DD, filial vaqti bo'yicha"),
    master_id: int | None = Query(None, description="bo'sh qoldirilsa, istalgan master"),
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    branch = await salon_branch(session, salon, branch_id)
    return await _slots(session, branch, parse_ids(service_ids), date, master_id)


@router.get("/available-dates", response_model=list[DateAvailability])
async def available_dates(
    branch_id: int,
    service_ids: str = Query(..., description="1,2,3"),
    master_id: int | None = None,
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    branch = await salon_branch(session, salon, branch_id)
    ids = parse_ids(service_ids)
    today = now_utc().astimezone(branch_tz(branch)).date()
    out = []
    for i in range(settings.booking_horizon_days + 1):
        day = today + timedelta(days=i)
        slots = await _slots(session, branch, ids, day, master_id)
        out.append(DateAvailability(date=day, has_slots=bool(slots.slots)))
    return out


# ---------- client login: phone + SMS code (only where SMS is switched on) ----------

def _sms_login_on() -> None:
    # With SMS off a code could be issued but never delivered, and then guessed: no code login at all.
    if settings.sms_backend == "none":
        raise HTTPException(404, "SMS orqali kirish o'chirilgan. Telegram orqali kiring.")


@router.post("/auth/request-code", status_code=204)
async def request_code(
    body: CodeRequest, request: Request, salon: Salon = Depends(active_salon), session: AsyncSession = Depends(get_session)
):
    _sms_login_on()
    ip = client_ip(request)
    if OTP_PER_IP.blocked(ip):  # one address asking codes for many numbers: SMS pumping
        raise HTTPException(429, "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring.")
    try:
        phone = auth.normalize_phone(body.phone)
        code = await auth.request_code(session, salon, phone)
    except auth.AuthError as e:
        raise HTTPException(e.status, str(e))
    OTP_PER_IP.hit(ip)
    try:
        await get_sms().send(phone, f"{salon.name}: tasdiqlash kodi {code}. Kodni hech kimga bermang.")
    except Exception:
        raise HTTPException(502, "SMS yuborib bo'lmadi. Birozdan keyin qayta urinib ko'ring.")
    return Response(status_code=204)


@router.post("/auth/verify", response_model=UserOut)
async def verify(
    body: CodeVerify, response: Response, salon: Salon = Depends(active_salon), session: AsyncSession = Depends(get_session)
):
    _sms_login_on()
    try:
        phone = auth.normalize_phone(body.phone)
        user = await auth.verify_code(session, salon, phone, body.code, body.name)
    except auth.AuthError as e:
        raise HTTPException(e.status, str(e))
    auth.set_cookie(response, auth.CLIENT_COOKIE, issue_token("client", user.id, salon.id), auth.cookie_path(salon))
    return user


@router.get("/auth/me", response_model=UserOut)
async def me(user: User = Depends(auth.current_client)):
    return user


@router.post("/auth/logout", status_code=204)
async def logout(salon: Salon = Depends(get_salon)):
    response = Response(status_code=204)
    auth.clear_cookie(response, auth.CLIENT_COOKIE, auth.cookie_path(salon))
    return response


# ---------- client bookings ----------

@router.post("/bookings", response_model=BookingOut, status_code=201)
async def create_booking(
    body: BookingCreate,
    background: BackgroundTasks,
    response: Response,
    idempotency_key: str | None = Header(None, max_length=64),
    salon: Salon = Depends(active_salon),
    user: User = Depends(auth.current_client),
    session: AsyncSession = Depends(get_session),
):
    try:
        booking, created = await svc.create_booking(
            session,
            salon_id=salon.id,
            user=user,
            branch_id=body.branch_id,
            master_id=body.master_id,
            service_ids=body.service_ids,
            starts_at=body.starts_at,
            comment=body.comment,
            idempotency_key=idempotency_key,
        )
    except svc.SlotTaken:
        raise HTTPException(409, SLOT_TAKEN_MESSAGE)
    except svc.BookingError as e:
        raise HTTPException(422, str(e))
    if created:
        background.add_task(send_confirmation, booking.id)
    else:
        response.status_code = 200  # the same request again: nothing new was booked
    return BookingOut.of(booking)


@router.get("/me/bookings", response_model=list[BookingOut])
async def my_bookings(user: User = Depends(auth.current_client), session: AsyncSession = Depends(get_session)):
    rows = await session.scalars(
        select(Booking).where(Booking.user_id == user.id).order_by(Booking.starts_at.desc()).limit(50)
    )
    return [BookingOut.of(b) for b in rows.unique()]


@router.post("/bookings/{booking_id}/cancel", response_model=BookingOut)
async def cancel(booking_id: int, user: User = Depends(auth.current_client), session: AsyncSession = Depends(get_session)):
    booking = await session.get(Booking, booking_id)
    if booking is None or booking.user_id != user.id:
        raise HTTPException(404, "Bron topilmadi")
    try:
        booking = await svc.cancel_booking(session, booking, by_staff=False)
    except svc.BookingError as e:
        raise HTTPException(422, str(e))
    return BookingOut.of(await svc.load_booking(session, booking.id))


@router.post("/bookings/{booking_id}/review", response_model=ReviewOut, status_code=201)
async def review(
    booking_id: int,
    body: ReviewCreate,
    user: User = Depends(auth.current_client),
    session: AsyncSession = Depends(get_session),
):
    booking = await session.get(Booking, booking_id)
    if booking is None or booking.user_id != user.id:
        raise HTTPException(404, "Bron topilmadi")
    try:
        r = await profiles.create_review(session, user, booking, body.rating, body.comment)
    except profiles.ReviewError as e:
        raise HTTPException(422, str(e))
    booking = await svc.load_booking(session, booking_id)
    return profiles.review_out(r, booking, booking.master)
