from datetime import datetime, timedelta
from typing import NamedTuple

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ACTIVE_STATUSES, Booking, BookingItem, Branch, Reminder, User
from app.services.availability import (
    NotOffered,
    branch_tz,
    eligible_masters,
    master_day_slots,
    now_utc,
    quote,
)

ADVISORY_LOCK_NS = 7001  # namespace for pg_advisory_xact_lock(ns, master_id)
REMINDERS = (("24h", timedelta(hours=24)), ("2h", timedelta(hours=2)))


class SlotTaken(Exception):
    pass


class BookingError(Exception):
    pass


def _is_sqlstate(exc: IntegrityError, code: str) -> bool:
    orig = exc.orig
    for candidate in (orig, getattr(orig, "__cause__", None)):
        if candidate is not None and getattr(candidate, "sqlstate", None) == code:
            return True
    return False


async def _existing_by_key(session: AsyncSession, user_id: int, key: str) -> Booking | None:
    return await session.scalar(
        select(Booking).where(Booking.user_id == user_id, Booking.idempotency_key == key)
    )


class Result(NamedTuple):
    booking: Booking
    created: bool  # False when the same request (Idempotency-Key) was already booked


async def create_booking(
    session: AsyncSession,
    *,
    salon_id: int,
    user: User,
    branch_id: int,
    master_id: int | None,
    service_ids: list[int],
    starts_at: datetime,
    source: str = "web",
    comment: str = "",
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> Result:
    """Create a booking, guaranteeing no double booking.

    Three layers: a per-master advisory lock serialises concurrent attempts,
    the slot is re-checked inside that lock, and the bookings_no_overlap
    exclusion constraint rejects anything that still slips through.
    With master_id=None ("any master") the first free capable master wins.
    """
    if starts_at.tzinfo is None:
        raise BookingError("starts_at vaqt zonasi bilan yuborilishi kerak")
    if user.salon_id != salon_id:
        raise BookingError("Mijoz bu salonga tegishli emas")
    if idempotency_key and (existing := await _existing_by_key(session, user.id, idempotency_key)):
        return Result(existing, False)

    branch = await session.get(Branch, branch_id)
    if branch is None or not branch.is_active or branch.salon_id != salon_id:
        raise BookingError("Filial topilmadi")
    tz = branch_tz(branch)
    day = starts_at.astimezone(tz).date()

    master_ids = [m.id for m in await eligible_masters(session, branch_id, service_ids)]
    if master_id is not None:
        if master_id not in master_ids:
            raise BookingError("Bu master tanlangan xizmatlarni bajarmaydi")
        master_ids = [master_id]
    user_id = user.id
    await session.rollback()  # each attempt below runs in its own transaction

    for mid in master_ids:
        try:
            await session.execute(select(func.pg_advisory_xact_lock(ADVISORY_LOCK_NS, mid)))
            # A retry of the same request may have waited on this lock while the first one booked.
            if idempotency_key and (existing := await _existing_by_key(session, user_id, idempotency_key)):
                await session.commit()
                return Result(existing, False)
            try:
                q = await quote(session, branch_id, mid, service_ids)
            except NotOffered as e:
                raise BookingError(str(e)) from e
            # Staff may seat a walk-in right now; clients must book a little ahead.
            lead = 0 if source == "admin" else None
            slots = await master_day_slots(session, mid, tz, day, q.duration, now, lead_min=lead)
            if starts_at not in slots:
                await session.rollback()
                continue

            booking = Booking(
                salon_id=salon_id,
                user_id=user_id,
                master_id=mid,
                branch_id=branch_id,
                starts_at=starts_at,
                ends_at=starts_at + q.duration,
                total_price=q.price,
                status="confirmed",
                source=source,
                comment=comment,
                idempotency_key=idempotency_key,
                items=[
                    BookingItem(
                        service_id=i.service_id, service_name=i.service_name, price=i.price, duration_min=i.duration_min
                    )
                    for i in q.items
                ],
            )
            session.add(booking)
            await session.flush()
            current = now or now_utc()
            for kind, before in REMINDERS:
                if starts_at - before > current:
                    session.add(Reminder(booking_id=booking.id, kind=kind, send_at=starts_at - before))
            await session.commit()
            return Result(await load_booking(session, booking.id), True)
        except IntegrityError as e:
            await session.rollback()
            if _is_sqlstate(e, "23P01"):  # exclusion_violation: overlap
                continue
            if idempotency_key and _is_sqlstate(e, "23505"):  # same request sent twice
                existing = await _existing_by_key(session, user_id, idempotency_key)
                if existing:
                    return Result(existing, False)
            raise
        except BaseException:
            await session.rollback()
            raise
    if idempotency_key and (existing := await _existing_by_key(session, user_id, idempotency_key)):
        return Result(existing, False)
    raise SlotTaken()


async def load_booking(session: AsyncSession, booking_id: int) -> Booking:
    booking = await session.scalar(
        select(Booking).where(Booking.id == booking_id).execution_options(populate_existing=True)
    )
    assert booking is not None
    return booking


async def cancel_booking(session: AsyncSession, booking: Booking, *, by_staff: bool, now: datetime | None = None) -> Booking:
    now = now or now_utc()
    if booking.status not in ACTIVE_STATUSES:
        raise BookingError("Bu bron allaqachon faol emas")
    if not by_staff and booking.starts_at - now < timedelta(hours=settings.cancel_deadline_hours):
        raise BookingError(
            f"Bronni boshlanishiga {settings.cancel_deadline_hours} soatdan kam qolganda bekor qilib bo'lmaydi. "
            "Iltimos, salonga qo'ng'iroq qiling."
        )
    booking.status = "cancelled"
    booking.cancelled_at = now
    await session.execute(delete(Reminder).where(Reminder.booking_id == booking.id, Reminder.sent_at.is_(None)))
    await session.commit()
    return booking


EARLY_ARRIVAL = timedelta(minutes=15)


async def mark_visit(session: AsyncSession, booking: Booking, status: str, now: datetime | None = None) -> None:
    """Client came (completed) or did not (no_show). Only once the visit has started,
    so a future slot is never freed by mistake; the two can be swapped to fix a wrong tap
    until the client has left a review."""
    now = now or now_utc()
    if booking.status in ACTIVE_STATUSES:
        if booking.starts_at - EARLY_ARRIVAL > now:
            raise BookingError("Tashrif vaqti hali kelmagan")
    elif booking.status in ("completed", "no_show"):
        if booking.review is not None:
            raise BookingError("Mijoz baho qoldirgan, holatni o'zgartirib bo'lmaydi")
    else:
        raise BookingError("Bekor qilingan bronni o'zgartirib bo'lmaydi")
    booking.status = status
    await session.execute(delete(Reminder).where(Reminder.booking_id == booking.id, Reminder.sent_at.is_(None)))
    await session.commit()


async def move_booking(
    session: AsyncSession, booking: Booking, *, starts_at: datetime, master_id: int | None = None, now: datetime | None = None
) -> Booking:
    """Staff move a booking to another time (and maybe another master of the same branch).
    The services and prices stay as booked; the new master must do all of them."""
    if starts_at.tzinfo is None:
        raise BookingError("starts_at vaqt zonasi bilan yuborilishi kerak")
    if booking.status not in ACTIVE_STATUSES:
        raise BookingError("Faqat faol bronni ko'chirish mumkin")
    mid = master_id or booking.master_id
    service_ids = [i.service_id for i in booking.items]
    if mid != booking.master_id and mid not in [m.id for m in await eligible_masters(session, booking.branch_id, service_ids)]:
        raise BookingError("Bu master tanlangan xizmatlarni bajarmaydi")
    booking_id, old_master, duration = booking.id, booking.master_id, booking.ends_at - booking.starts_at
    tz = branch_tz(booking.branch)
    await session.rollback()
    try:
        for lock_id in sorted({mid, old_master}):
            await session.execute(select(func.pg_advisory_xact_lock(ADVISORY_LOCK_NS, lock_id)))
        await session.execute(select(Booking.id).where(Booking.id == booking_id).with_for_update())
        b = await session.scalar(select(Booking).where(Booking.id == booking_id).execution_options(populate_existing=True))
        if b is None or b.status not in ACTIVE_STATUSES:
            raise BookingError("Faqat faol bronni ko'chirish mumkin")
        b.status = "cancelled"  # its own time must not count as busy while we look for the new one
        await session.flush()
        slots = await master_day_slots(session, mid, tz, starts_at.astimezone(tz).date(), duration, now, lead_min=0)
        if starts_at not in slots:
            raise SlotTaken()
        b.status, b.master_id, b.starts_at, b.ends_at = "confirmed", mid, starts_at, starts_at + duration
        await session.execute(delete(Reminder).where(Reminder.booking_id == b.id, Reminder.sent_at.is_(None)))
        await session.flush()
        current = now or now_utc()
        for kind, before in REMINDERS:
            if starts_at - before > current:
                await session.execute(delete(Reminder).where(Reminder.booking_id == b.id, Reminder.kind == kind))
                session.add(Reminder(booking_id=b.id, kind=kind, send_at=starts_at - before))
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if _is_sqlstate(e, "23P01"):
            raise SlotTaken() from e
        raise
    except BaseException:
        await session.rollback()
        raise
    return await load_booking(session, booking_id)
