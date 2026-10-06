from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    ACTIVE_STATUSES,
    Booking,
    Branch,
    BranchService,
    Master,
    MasterService,
    ScheduleException,
    Service,
    WorkingHours,
)
from app.scheduling import Interval, candidate_starts, merge, subtract


class NotOffered(Exception):
    """The master or branch does not provide the requested services."""


@dataclass
class QuoteItem:
    service_id: int
    service_name: str
    price: int
    duration_min: int


@dataclass
class Quote:
    items: list[QuoteItem]

    @property
    def duration(self) -> timedelta:
        return timedelta(minutes=sum(i.duration_min for i in self.items))

    @property
    def price(self) -> int:
        return sum(i.price for i in self.items)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def quote(session: AsyncSession, branch_id: int, master_id: int, service_ids: list[int]) -> Quote:
    """Price and duration of the chosen services when done by this master."""
    wanted = sorted(set(service_ids))
    if not wanted:
        raise NotOffered("Kamida bitta xizmat tanlang")
    rows = (
        await session.execute(
            select(
                Service.id,
                Service.name,
                func.coalesce(MasterService.price_override, BranchService.price),
                func.coalesce(MasterService.duration_override, BranchService.duration_min),
            )
            .join(BranchService, BranchService.service_id == Service.id)
            .join(MasterService, MasterService.service_id == Service.id)
            .where(
                BranchService.branch_id == branch_id,
                BranchService.is_active,
                Service.is_active,
                MasterService.master_id == master_id,
                Service.id.in_(wanted),
            )
            .order_by(Service.id)
        )
    ).all()
    if len(rows) != len(wanted):
        raise NotOffered("Bu master tanlangan xizmatlarni bajarmaydi")
    return Quote([QuoteItem(r[0], r[1], int(r[2]), int(r[3])) for r in rows])


async def eligible_masters(session: AsyncSession, branch_id: int, service_ids: list[int]) -> list[Master]:
    """Active masters in the branch who perform every requested service."""
    wanted = sorted(set(service_ids))
    stmt = select(Master).where(Master.branch_id == branch_id, Master.is_active).order_by(Master.id)
    if wanted:
        capable = (
            select(MasterService.master_id)
            .join(
                BranchService,
                (BranchService.service_id == MasterService.service_id) & (BranchService.branch_id == branch_id),
            )
            .where(MasterService.service_id.in_(wanted), BranchService.is_active)
            .group_by(MasterService.master_id)
            .having(func.count() == len(wanted))
        )
        stmt = stmt.where(Master.id.in_(capable))
    return list((await session.scalars(stmt)).all())


def branch_tz(branch: Branch) -> ZoneInfo:
    return ZoneInfo(branch.timezone or settings.default_timezone)


async def working_intervals(session: AsyncSession, master_id: int, day: date, tz: ZoneInfo) -> list[Interval]:
    def at(t) -> datetime:
        return datetime.combine(day, t, tzinfo=tz)

    hours = (
        await session.scalars(
            select(WorkingHours).where(WorkingHours.master_id == master_id, WorkingHours.weekday == day.weekday())
        )
    ).all()
    exceptions = (
        await session.scalars(
            select(ScheduleException).where(ScheduleException.master_id == master_id, ScheduleException.date == day)
        )
    ).all()
    if any(e.kind == "day_off" for e in exceptions):
        return []
    work = [(at(h.start_time), at(h.end_time)) for h in hours]
    work += [(at(e.start_time), at(e.end_time)) for e in exceptions if e.kind == "extra"]
    breaks = [(at(e.start_time), at(e.end_time)) for e in exceptions if e.kind == "break"]
    return subtract(merge(work), breaks)


async def busy_intervals(session: AsyncSession, master_id: int, start: datetime, end: datetime) -> list[Interval]:
    rows = await session.execute(
        select(Booking.starts_at, Booking.ends_at).where(
            Booking.master_id == master_id,
            Booking.status.in_(ACTIVE_STATUSES),
            Booking.starts_at < end,
            Booking.ends_at > start,
        )
    )
    return [(r[0], r[1]) for r in rows]


async def master_day_slots(
    session: AsyncSession,
    master_id: int,
    tz: ZoneInfo,
    day: date,
    duration: timedelta,
    now: datetime | None = None,
    lead_min: int | None = None,
) -> list[datetime]:
    """Real free start times for one master on one local day."""
    now = now or now_utc()
    today = now.astimezone(tz).date()
    if day < today or day > today + timedelta(days=settings.booking_horizon_days):
        return []
    work = await working_intervals(session, master_id, day, tz)
    if not work:
        return []
    busy = [(s.astimezone(tz), e.astimezone(tz)) for s, e in await busy_intervals(session, master_id, work[0][0], work[-1][1])]
    free = subtract(work, busy)
    lead = settings.min_lead_min if lead_min is None else lead_min
    not_before = (now + timedelta(minutes=lead)).astimezone(tz)
    return candidate_starts(free, duration, settings.slot_step_min, not_before)
