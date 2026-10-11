"""Salon, branch and barber profiles: photos, experience, location."""

import secrets

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.kinds import kind_of
from app.models import Booking, Branch, Master, Photo, Review, Salon, User
from app.schemas import PhotoOut, ReviewOut, SalonInfo
from app.tenancy import subscription_active
from app.text import strip_controls

MAX_PHOTO_BYTES = 3 * 1024 * 1024
LIMITS = {"branch": 8, "work": 30}  # photos per branch / per barber
SINGLE = {"cover", "logo", "master"}  # one photo per slot; a new upload replaces the old one


class PhotoError(Exception):
    pass


def url_of(photo_id: str) -> str:
    return f"/media/{photo_id}"


def salon_info(salon: Salon) -> SalonInfo:
    return SalonInfo(
        slug=salon.slug, name=salon.name, phone=salon.phone, paid_until=salon.paid_until,
        active=subscription_active(salon), description=salon.description, instagram=salon.instagram,
        cover_url=salon.cover_url, logo_url=salon.logo_url,
        telegram_bot=salon.tg_bot_username if salon.tg_token_sealed else "", kind=salon.kind or "barber",
        term=kind_of(salon.kind).term, client_word=kind_of(salon.kind).client, modules=(salon.modules or "booking").split(","),
        sms_login=settings.sms_backend != "none",
    )


def sniff(data: bytes) -> str:
    """The real image type from the file's first bytes; anything else is refused."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise PhotoError("Faqat JPG, PNG yoki WEBP rasm yuklang")


async def photos_by_ref(session: AsyncSession, salon_id: int, kind: str, ref_ids: list[int]) -> dict[int, list[PhotoOut]]:
    out: dict[int, list[PhotoOut]] = {r: [] for r in ref_ids}
    if not ref_ids:
        return out
    rows = await session.execute(
        select(Photo.id, Photo.ref_id, Photo.caption)
        .where(Photo.salon_id == salon_id, Photo.kind == kind, Photo.ref_id.in_(ref_ids))
        .order_by(Photo.created_at.desc())
    )
    for pid, ref, caption in rows:
        out[ref].append(PhotoOut(id=pid, url=url_of(pid), caption=caption))
    return out


def _set_slot(salon: Salon, target, kind: str, url: str | None) -> None:
    if kind == "cover":
        salon.cover_url = url
    elif kind == "logo":
        salon.logo_url = url
    elif kind == "master":
        target.photo_url = url


async def save_photo(
    session: AsyncSession, salon: Salon, kind: str, target: Branch | Master | None, data: bytes, caption: str = ""
) -> Photo:
    if not data:
        raise PhotoError("Rasm bo'sh")
    if len(data) > MAX_PHOTO_BYTES:
        raise PhotoError("Rasm juda katta (3 MB dan oshmasin)")
    content_type = sniff(data)
    ref_id = target.id if target is not None else None
    same_slot = select(Photo).where(Photo.salon_id == salon.id, Photo.kind == kind, Photo.ref_id.is_(ref_id) if ref_id is None else Photo.ref_id == ref_id)
    if kind in SINGLE:
        for old in (await session.scalars(same_slot)).all():
            await session.delete(old)
    elif kind in LIMITS:
        count = await session.scalar(select(func.count()).select_from(same_slot.subquery()))
        if count >= LIMITS[kind]:
            raise PhotoError(f"Ko'pi bilan {LIMITS[kind]} ta rasm. Avval eskisini o'chiring.")
    photo = Photo(id=secrets.token_hex(16), salon_id=salon.id, kind=kind, ref_id=ref_id,
                  content_type=content_type, data=data, size=len(data), caption=caption.strip()[:120])
    session.add(photo)
    _set_slot(salon, target, kind, url_of(photo.id))
    if kind == "branch" and not target.photo_url:
        target.photo_url = url_of(photo.id)
    await session.flush()
    return photo


async def delete_photo(session: AsyncSession, salon: Salon, photo: Photo, target: Branch | Master | None) -> None:
    url = url_of(photo.id)
    await session.delete(photo)
    await session.flush()
    if photo.kind in SINGLE:
        _set_slot(salon, target, photo.kind, None)
    elif photo.kind == "branch" and target is not None and target.photo_url == url:
        nxt = await session.scalar(
            select(Photo.id).where(Photo.salon_id == salon.id, Photo.kind == "branch", Photo.ref_id == target.id)
            .order_by(Photo.created_at).limit(1)
        )
        target.photo_url = url_of(nxt) if nxt else None


# ---------- reviews ----------

class ReviewError(Exception):
    pass


def short_name(name: str) -> str:
    """Reviews show "Aziz K.", never the full name or phone."""
    parts = (name or "").split()
    if not parts:
        return "Mijoz"
    return parts[0] + (f" {parts[1][0]}." if len(parts) > 1 else "")


async def ratings(session: AsyncSession, master_ids: list[int]) -> dict[int, tuple[float | None, int]]:
    out = {m: (None, 0) for m in master_ids}
    if master_ids:
        rows = await session.execute(
            select(Review.master_id, func.avg(Review.rating), func.count())
            .where(Review.master_id.in_(master_ids), Review.is_hidden.is_(False))
            .group_by(Review.master_id)
        )
        for mid, avg, n in rows:
            out[mid] = (round(float(avg), 1), n)
    return out


def review_out(r: Review, booking: Booking | None = None, master: Master | None = None) -> ReviewOut:
    return ReviewOut(
        id=r.id, rating=r.rating, comment=r.comment, created_at=r.created_at, is_hidden=r.is_hidden,
        client_name=short_name(booking.user.name if booking else ""),
        services=" + ".join(i.service_name for i in booking.items) if booking else "",
        master_id=r.master_id, master_name=master.name if master else "",
    )


async def create_review(session: AsyncSession, user: User, booking: Booking, rating: int, comment: str) -> Review:
    if booking.status != "completed":
        raise ReviewError("Baho faqat tashrifdan keyin qoldiriladi")
    if booking.review is not None:
        raise ReviewError("Bu tashrifga baho allaqachon qo'yilgan")
    review = Review(salon_id=booking.salon_id, booking_id=booking.id, master_id=booking.master_id, user_id=user.id,
                    rating=rating, comment=strip_controls(comment).strip())
    session.add(review)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ReviewError("Bu tashrifga baho allaqachon qo'yilgan")
    return review


async def list_reviews(session: AsyncSession, salon_id: int, *, master_id: int | None = None,
                       include_hidden: bool = False, limit: int = 30) -> list[ReviewOut]:
    stmt = (
        select(Review, Booking, Master)
        .join(Booking, Booking.id == Review.booking_id)
        .join(Master, Master.id == Review.master_id)
        .where(Review.salon_id == salon_id)
        .order_by(Review.created_at.desc())
        .limit(limit)
    )
    if master_id is not None:
        stmt = stmt.where(Review.master_id == master_id)
    if not include_hidden:
        stmt = stmt.where(Review.is_hidden.is_(False))
    return [review_out(r, b, m) for r, b, m in (await session.execute(stmt)).unique()]
