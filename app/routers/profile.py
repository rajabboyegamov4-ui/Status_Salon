"""Filling in the salon's public profile from the panel: photos, barber experience and works, branch location.

Owner/admin edit everything; a barber edits only their own photo, bio and works.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth
from app.db import get_session
from app.models import Booking, Branch, Master, Photo, Review, Salon, Staff
from app.routers.staff import manager_writer, writer
from app.schemas import (
    BranchOut,
    BranchUpdate,
    MasterOut,
    MasterProfileUpdate,
    PhotoOut,
    ReviewOut,
    ReviewVisibility,
    SalonInfo,
    SalonProfileUpdate,
)
from app.services import profiles
from app.tenancy import get_salon

router = APIRouter(prefix="/s/{slug}/staff", tags=["profile"])
media = APIRouter(tags=["media"])


async def _branch(session: AsyncSession, salon: Salon, branch_id: int) -> Branch:
    branch = await session.get(Branch, branch_id)
    if branch is None or branch.salon_id != salon.id:
        raise HTTPException(404, "Filial topilmadi")
    return branch


async def _master(session: AsyncSession, salon: Salon, staff: Staff, master_id: int) -> Master:
    master = await session.get(Master, master_id)
    branch = await session.get(Branch, master.branch_id) if master else None
    if master is None or branch.salon_id != salon.id:
        raise HTTPException(404, "Usta topilmadi")
    if staff.role == "barber" and staff.master_id != master.id:
        raise HTTPException(403, "Faqat o'z profilingizni o'zgartira olasiz")
    return master


async def _master_out(session: AsyncSession, salon: Salon, master: Master) -> MasterOut:
    out = MasterOut.model_validate(master)
    out.works = (await profiles.photos_by_ref(session, salon.id, "work", [master.id]))[master.id]
    out.rating, out.reviews_count = (await profiles.ratings(session, [master.id]))[master.id]
    return out


@router.patch("/salon", response_model=SalonInfo)
async def update_salon(
    body: SalonProfileUpdate,
    staff: Staff = Depends(manager_writer),
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(salon, field, value.strip())
    await session.commit()
    return profiles.salon_info(salon)


@router.patch("/branches/{branch_id}", response_model=BranchOut)
async def update_branch(
    branch_id: int,
    body: BranchUpdate,
    staff: Staff = Depends(manager_writer),
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    branch = await _branch(session, salon, branch_id)
    data = body.model_dump(exclude_unset=True)
    if ("lat" in data) != ("lng" in data):
        raise HTTPException(422, "Joylashuv uchun kenglik va uzunlik birga kerak")
    for field, value in data.items():
        if field not in ("lat", "lng") and value is None:
            continue
        setattr(branch, field, value.strip() if isinstance(value, str) else value)
    await session.commit()
    out = BranchOut.model_validate(branch)
    out.photos = (await profiles.photos_by_ref(session, salon.id, "branch", [branch.id]))[branch.id]
    return out


@router.patch("/masters/{master_id}", response_model=MasterOut)
async def update_master(
    master_id: int,
    body: MasterProfileUpdate,
    staff: Staff = Depends(writer),
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    master = await _master(session, salon, staff, master_id)
    data = body.model_dump(exclude_unset=True)
    if "rank" in data and staff.role == "barber":
        raise HTTPException(403, "Darajani salon rahbari belgilaydi")
    for field, value in data.items():
        if value is None and field != "experience_years":
            continue  # these columns are never empty; null means "leave as is"
        if field == "specialties":
            value = ", ".join(x.strip()[:30] for x in (value or []) if x.strip())
        elif isinstance(value, str):
            value = value.strip()
        setattr(master, field, value)
    await session.commit()
    return await _master_out(session, salon, master)


@router.post("/photos", response_model=PhotoOut, status_code=201)
async def upload_photo(
    request: Request,
    kind: str = Query(pattern="^(cover|logo|branch|master|work)$"),
    ref_id: int | None = None,
    caption: str = Query("", max_length=120),
    staff: Staff = Depends(writer),
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    """The image itself is the request body (Content-Type image/jpeg, image/png or image/webp)."""
    target = None
    if kind in ("cover", "logo"):
        if staff.role == "barber":
            raise HTTPException(403, "Salon rasmini rahbar yoki administrator yuklaydi")
    elif kind == "branch":
        if staff.role == "barber":
            raise HTTPException(403, "Filial rasmini rahbar yoki administrator yuklaydi")
        target = await _branch(session, salon, ref_id or 0)
    else:
        target = await _master(session, salon, staff, ref_id if ref_id is not None else (staff.master_id or 0))
    # stop reading past the limit: one huge upload must not fill the server's memory
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > profiles.MAX_PHOTO_BYTES:
            raise HTTPException(413, "Rasm juda katta (3 MB dan oshmasin)")
    body = bytes(body)
    try:
        photo = await profiles.save_photo(session, salon, kind, target, body, caption)
    except profiles.PhotoError as e:
        raise HTTPException(422, str(e))
    await session.commit()
    return PhotoOut(id=photo.id, url=profiles.url_of(photo.id), caption=photo.caption)


@router.delete("/photos/{photo_id}", status_code=204)
async def remove_photo(
    photo_id: str,
    staff: Staff = Depends(writer),
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    photo = await session.get(Photo, photo_id)
    if photo is None or photo.salon_id != salon.id:
        raise HTTPException(404, "Rasm topilmadi")
    target = None
    if photo.kind in ("master", "work"):
        target = await _master(session, salon, staff, photo.ref_id)
    elif staff.role == "barber":
        raise HTTPException(403, "Bu rasmni rahbar yoki administrator o'chiradi")
    elif photo.kind == "branch":
        target = await _branch(session, salon, photo.ref_id)
    await profiles.delete_photo(session, salon, photo, target)
    await session.commit()
    return Response(status_code=204)


@router.get("/reviews", response_model=list[ReviewOut])
async def staff_reviews(
    staff: Staff = Depends(auth.current_staff),
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    """A barber sees reviews of their own work; owner/admin see the whole salon, hidden ones included."""
    master_id = staff.master_id if staff.role == "barber" else None
    return await profiles.list_reviews(session, salon.id, master_id=master_id, include_hidden=True, limit=100)


@router.patch("/reviews/{review_id}", response_model=ReviewOut)
async def hide_review(
    review_id: int,
    body: ReviewVisibility,
    staff: Staff = Depends(manager_writer),
    salon: Salon = Depends(get_salon),
    session: AsyncSession = Depends(get_session),
):
    r = await session.get(Review, review_id)
    if r is None or r.salon_id != salon.id:
        raise HTTPException(404, "Otziv topilmadi")
    r.is_hidden = body.is_hidden
    await session.commit()
    booking = await session.get(Booking, r.booking_id)
    return profiles.review_out(r, booking, await session.get(Master, r.master_id))


@media.get("/media/{photo_id}", include_in_schema=False)
async def serve_photo(photo_id: str, session: AsyncSession = Depends(get_session)):
    row = (
        await session.execute(select(Photo.content_type, Photo.data).where(Photo.id == photo_id))
    ).first()
    if row is None:
        raise HTTPException(404)
    # Ids are random and never reused, so the browser may keep the file forever.
    return Response(row.data, media_type=row.content_type, headers={"Cache-Control": "public, max-age=31536000, immutable"})
