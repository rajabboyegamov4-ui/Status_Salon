"""Panel tools a real salon needs from day one: weekly hours, days off and breaks, services and
prices, walk-in slots from right now, and moving a booking to another time.

/api/s/{slug}/staff/...  Owner and administrator manage everyone; a barber manages only their own
breaks and days off and sees only their own data.
"""

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth
from app.db import get_session
from app.models import (
    ACTIVE_STATUSES,
    Booking,
    Branch,
    BranchService,
    Master,
    MasterService,
    Salon,
    ScheduleException,
    Service,
    Staff,
    WorkingHours,
)
from app.notifications import notify_staff, send_moved
from app.routers.public import SLOT_TAKEN_MESSAGE, _slots, parse_ids, salon_branch
from app.routers.staff import manager_writer, writer
from app.schemas import AdminBookingOut, SlotsOut
from app.services import booking as svc
from app.services.availability import branch_tz, working_intervals
from app.tenancy import get_salon

router = APIRouter(prefix="/s/{slug}/staff", tags=["schedule"])


async def _master(session: AsyncSession, salon: Salon, staff: Staff, master_id: int) -> Master:
    """The master, only within this salon; a barber reaches only themselves."""
    m = await session.get(Master, master_id)
    branch = await session.get(Branch, m.branch_id) if m else None
    if m is None or branch is None or branch.salon_id != salon.id:
        raise HTTPException(404, "Usta topilmadi")
    if staff.role == "barber" and staff.master_id != m.id:
        raise HTTPException(403, "Faqat o'zingizning jadvalingizni o'zgartira olasiz")
    return m


def _hm(t: time | None) -> str | None:
    return t.strftime("%H:%M") if t else None


# ---------- weekly working hours ----------

class DayHours(BaseModel):
    weekday: int = Field(ge=0, le=6)  # 0 = Monday
    start: time
    end: time

    @model_validator(mode="after")
    def _order(self):
        if self.end <= self.start:
            raise ValueError("Ish tugash vaqti boshlanishidan keyin bo'lishi kerak")
        return self


class WeekHours(BaseModel):
    days: list[DayHours] = Field(max_length=14)  # a weekday left out is a day off


@router.get("/masters/{master_id}/hours", response_model=WeekHours)
async def get_hours(
    master_id: int, staff: Staff = Depends(auth.current_staff), salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    await _master(session, salon, staff, master_id)
    rows = (await session.scalars(
        select(WorkingHours).where(WorkingHours.master_id == master_id).order_by(WorkingHours.weekday, WorkingHours.start_time)
    )).all()
    return WeekHours(days=[DayHours(weekday=r.weekday, start=r.start_time, end=r.end_time) for r in rows])


@router.put("/masters/{master_id}/hours", response_model=WeekHours)
async def set_hours(
    master_id: int, body: WeekHours, staff: Staff = Depends(manager_writer), salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    await _master(session, salon, staff, master_id)
    by_day: dict[int, list[DayHours]] = {}
    for d in sorted(body.days, key=lambda d: (d.weekday, d.start)):
        if by_day.get(d.weekday) and by_day[d.weekday][-1].end > d.start:
            raise HTTPException(422, "Bir kunning ish vaqtlari ustma-ust tushmasin")
        by_day.setdefault(d.weekday, []).append(d)
    await session.execute(delete(WorkingHours).where(WorkingHours.master_id == master_id))
    for d in body.days:
        session.add(WorkingHours(master_id=master_id, weekday=d.weekday, start_time=d.start, end_time=d.end))
    await session.commit()
    return await get_hours(master_id, staff, salon, session)


# ---------- days off, breaks, extra hours ----------

class ExceptionIn(BaseModel):
    master_id: int
    date: date
    kind: str = Field(pattern="^(day_off|break|extra)$")
    start: time | None = None
    end: time | None = None
    note: str = Field("", max_length=255)

    @model_validator(mode="after")
    def _range(self):
        if self.kind == "day_off":
            self.start = self.end = None
        elif not self.start or not self.end or self.end <= self.start:
            raise ValueError("Boshlanish va tugash vaqtini to'g'ri kiriting")
        return self


class ExceptionOut(BaseModel):
    id: int
    master_id: int
    date: date
    kind: str
    start: str | None
    end: str | None
    note: str
    conflicts: int = 0  # active bookings inside it: the salon should call these clients


def _exc_out(e: ScheduleException, conflicts: int = 0) -> ExceptionOut:
    return ExceptionOut(id=e.id, master_id=e.master_id, date=e.date, kind=e.kind, start=_hm(e.start_time),
                        end=_hm(e.end_time), note=e.note, conflicts=conflicts)


async def _conflicts(session: AsyncSession, e: ScheduleException, tz) -> int:
    if e.kind == "extra":
        return 0
    start = datetime.combine(e.date, e.start_time or time(0), tzinfo=tz)
    end = datetime.combine(e.date, e.end_time, tzinfo=tz) if e.end_time else start + timedelta(days=1)
    rows = await session.scalars(select(Booking.id).where(
        Booking.master_id == e.master_id, Booking.status.in_(ACTIVE_STATUSES), Booking.starts_at < end, Booking.ends_at > start))
    return len(rows.all())


@router.get("/exceptions", response_model=list[ExceptionOut])
async def list_exceptions(
    date_from: date = Query(alias="from"), date_to: date = Query(alias="to"), master_id: int | None = None,
    staff: Staff = Depends(auth.current_staff), salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session),
):
    if (date_to - date_from).days > 62:
        raise HTTPException(422, "Oraliq 2 oydan oshmasin")
    stmt = (select(ScheduleException).join(Master, Master.id == ScheduleException.master_id).join(Branch, Branch.id == Master.branch_id)
            .where(Branch.salon_id == salon.id, ScheduleException.date >= date_from, ScheduleException.date <= date_to)
            .order_by(ScheduleException.date, ScheduleException.start_time))
    if staff.role == "barber":
        stmt = stmt.where(ScheduleException.master_id == staff.master_id)
    elif master_id is not None:
        stmt = stmt.where(ScheduleException.master_id == master_id)
    return [_exc_out(e) for e in (await session.scalars(stmt)).all()]


@router.post("/exceptions", response_model=ExceptionOut, status_code=201)
async def add_exception(
    body: ExceptionIn, staff: Staff = Depends(writer), salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session),
):
    m = await _master(session, salon, staff, body.master_id)
    branch = await session.get(Branch, m.branch_id)
    tz = branch_tz(branch)
    if body.date < datetime.now(tz).date():
        raise HTTPException(422, "O'tgan kunni o'zgartirib bo'lmaydi")
    if body.kind == "day_off":
        await session.execute(delete(ScheduleException).where(
            ScheduleException.master_id == m.id, ScheduleException.date == body.date, ScheduleException.kind == "day_off"))
    e = ScheduleException(master_id=m.id, date=body.date, kind=body.kind, start_time=body.start, end_time=body.end, note=body.note.strip())
    session.add(e)
    await session.flush()
    conflicts = await _conflicts(session, e, tz)
    await session.commit()
    return _exc_out(e, conflicts)


@router.delete("/exceptions/{exc_id}", status_code=204)
async def delete_exception(
    exc_id: int, staff: Staff = Depends(writer), salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session),
):
    e = await session.get(ScheduleException, exc_id)
    if e is None:
        raise HTTPException(404, "Topilmadi")
    await _master(session, salon, staff, e.master_id)
    await session.delete(e)
    await session.commit()


# ---------- one day: who works when (the board draws hours and breaks from this) ----------

class MasterDay(BaseModel):
    master_id: int
    intervals: list[tuple[str, str]]  # working time left after breaks, "HH:MM"


@router.get("/day-hours", response_model=list[MasterDay])
async def day_hours(
    branch_id: int, date: date, staff: Staff = Depends(auth.current_staff), salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    branch = await salon_branch(session, salon, branch_id)
    tz = branch_tz(branch)
    stmt = select(Master).where(Master.branch_id == branch.id).order_by(Master.id)
    if staff.role == "barber":
        stmt = stmt.where(Master.id == staff.master_id)
    out = []
    for m in (await session.scalars(stmt)).all():
        work = await working_intervals(session, m.id, date, tz)
        out.append(MasterDay(master_id=m.id, intervals=[(s.strftime("%H:%M"), e.strftime("%H:%M")) for s, e in work]))
    return out


# ---------- slots for staff: a walk-in can sit down right now ----------

@router.get("/available-slots", response_model=SlotsOut)
async def staff_slots(
    branch_id: int, service_ids: str, date: date, master_id: int | None = None,
    staff: Staff = Depends(auth.current_staff), salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session),
):
    if staff.role == "barber":
        master_id = staff.master_id
    branch = await salon_branch(session, salon, branch_id)
    return await _slots(session, branch, parse_ids(service_ids), date, master_id, lead_min=0)


# ---------- move a booking ----------

class MoveIn(BaseModel):
    starts_at: datetime
    master_id: int | None = None


@router.post("/bookings/{booking_id}/move", response_model=AdminBookingOut)
async def move(
    booking_id: int, body: MoveIn, background: BackgroundTasks, staff: Staff = Depends(writer),
    salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session),
):
    actor = staff.id  # read before any commit expires the row
    b = await session.get(Booking, booking_id)
    if b is None or b.salon_id != salon.id:
        raise HTTPException(404, "Bron topilmadi")
    if staff.role == "barber" and (b.master_id != staff.master_id or (body.master_id not in (None, staff.master_id))):
        raise HTTPException(403, "Faqat o'zingizning broningizni ko'chira olasiz")
    if body.master_id is not None:
        await _master(session, salon, staff, body.master_id)
    old = (b.starts_at, b.master_id)
    try:
        b = await svc.move_booking(session, b, starts_at=body.starts_at, master_id=body.master_id)
    except svc.SlotTaken:
        raise HTTPException(409, SLOT_TAKEN_MESSAGE)
    except svc.BookingError as e:
        raise HTTPException(422, str(e))
    if (b.starts_at, b.master_id) != old:
        background.add_task(send_moved, b.id)
        background.add_task(notify_staff, b.id, "moved", actor)
    return AdminBookingOut.of(b)


# ---------- services and prices ----------

class MasterPrice(BaseModel):
    master_id: int
    price: int | None = Field(None, ge=0, le=100_000_000)  # None: the branch price
    duration_min: int | None = Field(None, ge=5, le=600)


class ServiceOut(BaseModel):
    id: int
    name: str
    category: str
    description: str
    price: int
    duration_min: int
    is_active: bool
    masters: list[MasterPrice]


class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    price: int = Field(ge=0, le=100_000_000)
    duration_min: int = Field(ge=5, le=600)
    category: str = Field("", max_length=60)
    description: str = Field("", max_length=1000)
    masters: list[MasterPrice] = Field(default_factory=list, max_length=200)  # who does it


class ServicePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    price: int | None = Field(None, ge=0, le=100_000_000)
    duration_min: int | None = Field(None, ge=5, le=600)
    category: str | None = Field(None, max_length=60)
    description: str | None = Field(None, max_length=1000)
    is_active: bool | None = None
    masters: list[MasterPrice] | None = Field(None, max_length=200)


async def _branch_masters(session: AsyncSession, branch: Branch) -> set[int]:
    return set((await session.scalars(select(Master.id).where(Master.branch_id == branch.id))).all())


async def _service_out(session: AsyncSession, branch: Branch, sid: int) -> ServiceOut | None:
    row = (await session.execute(
        select(Service, BranchService).join(BranchService, BranchService.service_id == Service.id)
        .where(Service.id == sid, BranchService.branch_id == branch.id)
    )).first()
    if row is None:
        return None
    s, bs = row
    ms = (await session.scalars(
        select(MasterService).join(Master, Master.id == MasterService.master_id)
        .where(MasterService.service_id == sid, Master.branch_id == branch.id).order_by(MasterService.master_id)
    )).all()
    return ServiceOut(id=s.id, name=s.name, category=s.category, description=s.description, price=bs.price,
                      duration_min=bs.duration_min, is_active=s.is_active and bs.is_active,
                      masters=[MasterPrice(master_id=m.master_id, price=m.price_override, duration_min=m.duration_override) for m in ms])


async def _set_masters(session: AsyncSession, branch: Branch, sid: int, masters: list[MasterPrice]) -> None:
    allowed = await _branch_masters(session, branch)
    if any(m.master_id not in allowed for m in masters):
        raise HTTPException(422, "Usta bu filialda emas")
    await session.execute(delete(MasterService).where(MasterService.service_id == sid, MasterService.master_id.in_(allowed)))
    for m in {m.master_id: m for m in masters}.values():
        session.add(MasterService(master_id=m.master_id, service_id=sid, price_override=m.price, duration_override=m.duration_min))


@router.get("/services", response_model=list[ServiceOut])
async def list_services(
    branch_id: int, staff: Staff = Depends(auth.current_staff), salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    branch = await salon_branch(session, salon, branch_id)
    ids = (await session.scalars(
        select(Service.id).join(BranchService, BranchService.service_id == Service.id)
        .where(Service.salon_id == salon.id, BranchService.branch_id == branch.id).order_by(Service.category, Service.id)
    )).all()
    return [await _service_out(session, branch, i) for i in ids]


@router.post("/services", response_model=ServiceOut, status_code=201)
async def create_service(
    body: ServiceIn, branch_id: int, staff: Staff = Depends(manager_writer), salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    branch = await salon_branch(session, salon, branch_id)
    s = Service(salon_id=salon.id, name=body.name.strip(), category=body.category.strip(), description=body.description.strip())
    session.add(s)
    await session.flush()
    session.add(BranchService(branch_id=branch.id, service_id=s.id, price=body.price, duration_min=body.duration_min))
    await _set_masters(session, branch, s.id, body.masters)
    await session.commit()
    return await _service_out(session, branch, s.id)


@router.patch("/services/{service_id}", response_model=ServiceOut)
async def update_service(
    service_id: int, body: ServicePatch, branch_id: int, staff: Staff = Depends(manager_writer),
    salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session),
):
    """Price changes apply to new bookings; booked visits keep the price they were booked at."""
    branch = await salon_branch(session, salon, branch_id)
    s = await session.get(Service, service_id)
    bs = await session.get(BranchService, (branch.id, service_id))
    if s is None or s.salon_id != salon.id or bs is None:
        raise HTTPException(404, "Xizmat topilmadi")
    for field in ("name", "category", "description"):
        if (v := getattr(body, field)) is not None:
            setattr(s, field, v.strip())
    if body.price is not None:
        bs.price = body.price
    if body.duration_min is not None:
        bs.duration_min = body.duration_min
    if body.is_active is not None:
        bs.is_active = body.is_active
        s.is_active = s.is_active or body.is_active
    if body.masters is not None:
        await _set_masters(session, branch, service_id, body.masters)
    await session.commit()
    return await _service_out(session, branch, service_id)
