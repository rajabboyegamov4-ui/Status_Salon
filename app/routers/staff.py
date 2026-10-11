"""The salon panel: barbers, administrators and the owner log in with their own login + password.

/api/s/{slug}/staff/...  A barber sees only their own bookings; owner/admin see the whole salon.
"""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth
from app.db import get_session
from app.models import Booking, Branch, Master, Salon, Staff, User
from app.notifications import notify_staff, send_cancelled, send_confirmation, send_review_request
from app.routers.public import SLOT_TAKEN_MESSAGE
from app.schemas import (
    AdminBookingCreate,
    AdminBookingOut,
    PasswordChange,
    StaffLogin,
    StaffMe,
    StaffOut,
    StatusUpdate,
    TeamMemberCreate,
    TeamMemberUpdate,
)
from app.security import hash_password, verify_password
from app.services import booking as svc
from app.services import profiles, team
from app.services.availability import branch_tz
from app.tenancy import SUSPENDED_MESSAGE, get_salon, subscription_active

router = APIRouter(prefix="/s/{slug}/staff", tags=["staff"])
WALK_IN_PHONE = "walk-in"  # not a real number: never gets messages


async def _me(session: AsyncSession, staff: Staff, salon: Salon) -> StaffMe:
    master = await session.get(Master, staff.master_id) if staff.master_id else None
    return StaffMe(**StaffOut.model_validate(staff).model_dump(), salon=profiles.salon_info(salon),
                   branch_id=master.branch_id if master else None)


def _require_active(salon: Salon) -> None:
    if not subscription_active(salon):
        raise HTTPException(402, "Obuna muddati tugagan. Panel faqat ko'rish rejimida ishlaydi.")


async def writer(staff: Staff = Depends(auth.current_staff), salon: Salon = Depends(get_salon)) -> Staff:
    """Any staff member, while the salon's subscription is paid."""
    _require_active(salon)
    return staff


async def manager_writer(staff: Staff = Depends(auth.manager), salon: Salon = Depends(get_salon)) -> Staff:
    _require_active(salon)
    return staff


# ---------- session ----------

@router.post("/login", response_model=StaffMe)
async def login(
    body: StaffLogin, response: Response, salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)
):
    try:
        staff = await auth.staff_login(session, salon, body.login, body.password)
    except auth.AuthError as e:
        raise HTTPException(e.status, str(e))
    auth.set_cookie(response, auth.STAFF_COOKIE, auth.staff_token(staff), auth.cookie_path(salon))
    return await _me(session, staff, salon)


@router.post("/logout", status_code=204)
async def logout(salon: Salon = Depends(get_salon)):
    response = Response(status_code=204)
    auth.clear_cookie(response, auth.STAFF_COOKIE, auth.cookie_path(salon))
    return response


@router.get("/me", response_model=StaffMe)
async def me(staff: Staff = Depends(auth.current_staff), salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)):
    return await _me(session, staff, salon)


@router.post("/password", status_code=204)
async def change_password(
    body: PasswordChange,
    staff: Staff = Depends(auth.current_staff),
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    if not verify_password(body.old_password, staff.password_hash):
        raise HTTPException(400, "Eski parol noto'g'ri")
    staff.password_hash = hash_password(body.new_password)
    staff.token_version += 1  # other devices are signed out; this one gets a fresh session
    await session.commit()
    response = Response(status_code=204)
    auth.set_cookie(response, auth.STAFF_COOKIE, auth.staff_token(staff), auth.cookie_path(salon))
    return response


# ---------- bookings ----------

@router.get("/bookings", response_model=list[AdminBookingOut])
async def day_bookings(
    date: date,
    branch_id: int | None = None,
    staff: Staff = Depends(auth.current_staff),
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    branches = (await session.scalars(select(Branch).where(Branch.salon_id == salon.id).order_by(Branch.id))).all()
    if branch_id is not None:
        branches = [b for b in branches if b.id == branch_id]
        if not branches:
            raise HTTPException(404, "Filial topilmadi")
    if not branches:
        return []
    start = datetime.combine(date, datetime.min.time(), tzinfo=branch_tz(branches[0]))
    stmt = (
        select(Booking)
        .where(
            Booking.salon_id == salon.id,
            Booking.branch_id.in_([b.id for b in branches]),
            Booking.starts_at >= start,
            Booking.starts_at < start + timedelta(days=1),
        )
        .order_by(Booking.master_id, Booking.starts_at)
    )
    if staff.role == "barber":
        stmt = stmt.where(Booking.master_id == staff.master_id)
    return [AdminBookingOut.of(b) for b in (await session.scalars(stmt)).unique()]


@router.post("/bookings", response_model=AdminBookingOut, status_code=201)
async def create_for_client(
    body: AdminBookingCreate,
    background: BackgroundTasks,
    response: Response,
    idempotency_key: str | None = Header(None, max_length=64),
    staff: Staff = Depends(writer),
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    """A client who called or walked in; staff book on their behalf. A barber books only into their own chair."""
    if staff.role == "barber":
        if body.master_id not in (None, staff.master_id):
            raise HTTPException(403, "Faqat o'zingizga bron qo'sha olasiz")
        body.master_id = staff.master_id
    comment = body.comment
    if body.client_phone.strip():
        try:
            phone = auth.normalize_phone(body.client_phone)
        except auth.AuthError as e:
            raise HTTPException(422, str(e))
        name = body.client_name.strip()
    else:
        # No number given: one shared "walk-in" client per salon; the name goes into the note.
        phone, name = WALK_IN_PHONE, "Telefonsiz mijoz"
        if body.client_name.strip():
            comment = f"{body.client_name.strip()}. {comment}".strip(". ").strip()[:500]
    client = await session.scalar(select(User).where(User.salon_id == salon.id, User.phone == phone))
    if client is None:
        client = User(salon_id=salon.id, phone=phone, name=name)
        session.add(client)
        await session.commit()
    elif name and not client.name:
        client.name = name
        await session.commit()
    try:
        booking, created = await svc.create_booking(
            session,
            salon_id=salon.id,
            user=client,
            branch_id=body.branch_id,
            master_id=body.master_id,
            service_ids=body.service_ids,
            starts_at=body.starts_at,
            comment=comment,
            source="admin",
            idempotency_key=idempotency_key,
        )
    except svc.SlotTaken:
        raise HTTPException(409, SLOT_TAKEN_MESSAGE)
    except svc.BookingError as e:
        raise HTTPException(422, str(e))
    if created:
        background.add_task(send_confirmation, booking.id)
        background.add_task(notify_staff, booking.id, "new", staff.id)
    else:
        response.status_code = 200  # the same request again: nothing new was booked
    return AdminBookingOut.of(booking)


@router.patch("/bookings/{booking_id}", response_model=AdminBookingOut)
async def update_status(
    booking_id: int,
    body: StatusUpdate,
    background: BackgroundTasks,
    staff: Staff = Depends(writer),
    session: AsyncSession = Depends(get_session),
):
    # Lock the row first: two taps on different phones must not both change it.
    await session.execute(select(Booking.id).where(Booking.id == booking_id).with_for_update())
    booking = await session.get(Booking, booking_id)
    if booking is None or booking.salon_id != staff.salon_id:
        raise HTTPException(404, "Bron topilmadi")
    if staff.role == "barber" and booking.master_id != staff.master_id:
        raise HTTPException(403, "Bu sizning broningiz emas")
    if body.status == "cancelled":
        try:
            await svc.cancel_booking(session, booking, by_staff=True)
        except svc.BookingError as e:
            raise HTTPException(422, str(e))
        background.add_task(send_cancelled, booking_id)
        background.add_task(notify_staff, booking_id, "cancelled", staff.id)
    else:
        try:
            await svc.mark_visit(session, booking, body.status)
        except svc.BookingError as e:
            raise HTTPException(422, str(e))
        if body.status == "completed":
            background.add_task(send_review_request, booking_id)
    return AdminBookingOut.of(await svc.load_booking(session, booking_id))


# ---------- team: owner/admin create barber logins ----------

@router.get("/team", response_model=list[StaffOut])
async def team_list(staff: Staff = Depends(auth.manager), session: AsyncSession = Depends(get_session)):
    return (await session.scalars(select(Staff).where(Staff.salon_id == staff.salon_id).order_by(Staff.id))).all()


def _can_manage(actor: Staff, role: str) -> None:
    # The owner manages everyone; an administrator manages barbers only.
    if role == "owner" or (role == "admin" and actor.role != "owner"):
        raise HTTPException(403, "Bu xodimni faqat salon rahbari boshqaradi")


@router.post("/team", response_model=StaffOut, status_code=201)
async def team_create(
    body: TeamMemberCreate,
    staff: Staff = Depends(manager_writer),
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    _can_manage(staff, body.role)
    try:
        member = await team.create_member(
            session, salon, name=body.name.strip(), login=body.login, password=body.password,
            role=body.role, rank=body.rank, branch_id=body.branch_id,
        )
    except team.TeamError as e:
        raise HTTPException(409, str(e))
    await session.commit()
    return member


@router.patch("/team/{staff_id}", response_model=StaffOut)
async def team_update(
    staff_id: int,
    body: TeamMemberUpdate,
    staff: Staff = Depends(manager_writer),
    session: AsyncSession = Depends(get_session),
):
    member = await session.get(Staff, staff_id)
    if member is None or member.salon_id != staff.salon_id:
        raise HTTPException(404, "Xodim topilmadi")
    if member.id != staff.id:
        _can_manage(staff, member.role)
    elif body.is_active is False:
        raise HTTPException(422, "O'zingizni o'chira olmaysiz")
    master = await session.get(Master, member.master_id) if member.master_id else None
    if body.name is not None:
        member.name = body.name.strip()
        if master:
            master.name = member.name
    if body.is_active is not None:
        member.is_active = body.is_active
        member.token_version += 1
        if master:
            master.is_active = body.is_active  # an inactive barber disappears from the booking site
    if body.password is not None:
        member.password_hash = hash_password(body.password)
        member.token_version += 1
        member.failed_logins = 0
        member.locked_until = None
    await session.commit()
    return member
