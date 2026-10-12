"""The platform's own desk: the owner, a partner and sales people.

/api/platform/...
- Owner (PLATFORM_LOGIN) and partner: everything - subscriptions and money, the team.
- Sales: their own leads, demo salons (free trial), moving a demo bot to the next demo. They do not see money
  and cannot extend or stop a paying business.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth
from app.auth import PlatformPrincipal, platform_manager, platform_user
from app.db import get_session
from app.models import Booking, Lead, LeadNote, Payment, PlatformSetting, PlatformUser, Salon, Staff
from app.ratelimit import client_ip
from app.routers.telegram import BotError, attach_bot, refresh_menu
from app.schemas import (
    GoalIn,
    LeadImport,
    LeadImportOut,
    LeadIn,
    LeadNoteIn,
    LeadNoteOut,
    LeadOut,
    LeadPatch,
    PaymentOut,
    PlatformLogin,
    PlatformMe,
    PlatformSalonOut,
    SalonCreate,
    SalonUpdate,
    TakeBot,
    TeamIn,
    TeamOut,
    TeamPatch,
)
from app.security import hash_password
from app.services import team
from app.services.availability import now_utc
from app.tenancy import salon_today, subscription_active, validate_slug
from app.text import strip_controls
from app.telegram import unseal

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


async def _out(session: AsyncSession, salon: Salon, who: PlatformPrincipal) -> PlatformSalonOut:
    staff_count = await session.scalar(select(func.count()).where(Staff.salon_id == salon.id, Staff.is_active))
    bookings = await session.scalar(
        select(func.count()).where(Booking.salon_id == salon.id, Booking.created_at > now_utc() - timedelta(days=30))
    )
    paid = int(await session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.salon_id == salon.id)) or 0) if who.manager else None
    lead_id = await session.scalar(select(Lead.id).where(Lead.salon_id == salon.id))
    return PlatformSalonOut(
        id=salon.id, slug=salon.slug, name=salon.name, phone=salon.phone, plan=salon.plan, kind=salon.kind,
        paid_until=salon.paid_until, is_active=salon.is_active, active=subscription_active(salon), is_demo=salon.is_demo,
        telegram_bot=salon.tg_bot_username if salon.tg_token_sealed else "", created_at=salon.created_at,
        modules=(salon.modules or "booking").split(","),
        staff_count=staff_count or 0, bookings_30d=bookings or 0, paid_total=paid, lead_id=lead_id,
    )


# ---------- session ----------

@router.post("/login", status_code=204)
async def login(body: PlatformLogin, request: Request, session: AsyncSession = Depends(get_session)):
    try:
        token = await auth.platform_login(session, body.login, body.password, client_ip(request))
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


@router.get("/me", response_model=PlatformMe)
async def me(who: PlatformPrincipal = Depends(platform_user)):
    return PlatformMe(id=who.id, name=who.name, role=who.role)


# ---------- businesses ----------

@router.get("/salons", response_model=list[PlatformSalonOut])
async def salons(who: PlatformPrincipal = Depends(platform_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Salon).order_by(Salon.is_demo, Salon.paid_until, Salon.id))).all()
    return [await _out(session, s, who) for s in rows]


async def _my_lead(session: AsyncSession, who: PlatformPrincipal, lead_id: int) -> Lead:
    lead = await session.get(Lead, lead_id)
    if lead is None or (not who.manager and who.id not in (lead.assigned_to, lead.created_by)):
        raise HTTPException(404, "Lid topilmadi")
    return lead


@router.post("/salons", response_model=PlatformSalonOut, status_code=201)
async def create_salon(body: SalonCreate, who: PlatformPrincipal = Depends(platform_user), session: AsyncSession = Depends(get_session)):
    """Managers connect paying businesses; sales open demos only (free trial, no paid months)."""
    slug = validate_slug(body.slug)
    demo = body.is_demo or not who.manager
    months = body.months if who.manager and not demo else 0
    trial = body.trial_days or (14 if demo else 0)
    lead = await _my_lead(session, who, body.lead_id) if body.lead_id else None
    if lead is not None and lead.salon_id:
        raise HTTPException(409, "Bu lid uchun biznes allaqachon ochilgan")
    try:
        salon = await team.create_salon(
            session, slug=slug, name=body.name.strip(), phone=body.phone.strip(),
            paid_until=add_months(salon_today(), months) + timedelta(days=trial),
            branch_name=body.branch_name.strip(), branch_address=body.branch_address.strip(),
            owner_name=body.owner_name.strip(), owner_login=body.owner_login, owner_password=body.owner_password,
            kind=body.kind, sample_masters=body.sample_masters,
        )
    except team.TeamError as e:
        raise HTTPException(409, str(e))
    salon.is_demo = demo
    if lead is not None:
        lead.salon_id = salon.id
        if lead.status in ("new", "talked"):
            lead.status = "demo"
    await session.commit()
    await session.refresh(salon)
    return await _out(session, salon, who)


@router.patch("/salons/{salon_id}", response_model=PlatformSalonOut)
async def update_salon(salon_id: int, body: SalonUpdate, who: PlatformPrincipal = Depends(platform_manager),
                       session: AsyncSession = Depends(get_session)):
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
        if body.amount:
            session.add(Payment(salon_id=salon.id, amount=body.amount, months=body.extend_months, user_id=who.id))
            salon.is_demo = False  # it pays: no longer a demo, its bot stays with it
            lead = await session.scalar(select(Lead).where(Lead.salon_id == salon.id))
            if lead is not None:
                lead.status = "paid"
    if body.is_demo is not None:
        salon.is_demo = body.is_demo
    if body.modules is not None:
        salon.modules = ",".join(m for m in ("booking", "orders") if m in body.modules)
    if body.is_active is not None:
        salon.is_active = body.is_active
    await session.commit()
    await session.refresh(salon)
    if body.modules is not None:
        await refresh_menu(salon)
    return await _out(session, salon, who)


@router.post("/salons/{salon_id}/take-bot", response_model=PlatformSalonOut)
async def take_bot(salon_id: int, body: TakeBot, who: PlatformPrincipal = Depends(platform_user),
                   session: AsyncSession = Depends(get_session)):
    """Reuse a demo's bot for another demo, so nobody opens a new bot in @BotFather for every meeting.
    The bot can only come from a demo; sales can only give it to a demo."""
    target = await session.get(Salon, salon_id, with_for_update=True)
    source = await session.get(Salon, body.from_salon_id, with_for_update=True)
    if target is None or source is None or target.id == source.id:
        raise HTTPException(404, "Salon topilmadi")
    if not source.is_demo:
        raise HTTPException(409, "Botni faqat demo salondan olish mumkin: to'lovchi mijozning boti unda qoladi")
    if not who.manager and not target.is_demo:
        raise HTTPException(403, "Botni faqat demo salonga ulash mumkin")
    token = unseal(source.tg_token_sealed)
    if not token:
        raise HTTPException(409, "Bu demoda bot yo'q")
    try:
        await attach_bot(session, target, token, moving_from=source)
    except BotError as e:
        raise HTTPException(e.status, str(e))
    await session.commit()
    await session.refresh(target)
    return await _out(session, target, who)


# ---------- leads ----------

async def _lead_outs(session: AsyncSession, leads: list[Lead]) -> list[LeadOut]:
    """Names, demo addresses and conversation counts for many leads in three queries."""
    ids = [x.id for x in leads]
    names = dict((await session.execute(select(PlatformUser.id, PlatformUser.name))).all())
    slugs = dict((await session.execute(select(Salon.id, Salon.slug).where(
        Salon.id.in_([x.salon_id for x in leads if x.salon_id])))).all()) if ids else {}
    notes = {k: (n, d) for k, n, d in (await session.execute(
        select(LeadNote.lead_id, func.count(), func.max(LeadNote.day)).where(LeadNote.lead_id.in_(ids)).group_by(LeadNote.lead_id))).all()} if ids else {}
    return [LeadOut.model_validate(x).model_copy(update={
        "assigned_name": names.get(x.assigned_to, "") if x.assigned_to else "", "salon_slug": slugs.get(x.salon_id, ""),
        "notes_count": notes.get(x.id, (0, None))[0], "last_note_on": notes.get(x.id, (0, None))[1]}) for x in leads]


async def _lead_out(session: AsyncSession, lead: Lead) -> LeadOut:
    return (await _lead_outs(session, [lead]))[0]


TEXT_FIELDS = ("name", "phone", "contact", "address", "next_step", "note", "district", "source", "how_now", "lost_reason")


def _clean(data: dict) -> dict:
    return {k: (strip_controls(v).strip() if k in TEXT_FIELDS and isinstance(v, str) else v) for k, v in data.items()}


async def _check_assignee(session: AsyncSession, who: PlatformPrincipal, user_id: int | None) -> int | None:
    if not who.manager:
        return who.id  # a sales person's leads are their own
    if user_id is not None:
        user = await session.get(PlatformUser, user_id)
        if user is None or not user.is_active:
            raise HTTPException(422, "Bunday xodim yo'q")
    return user_id


@router.get("/leads", response_model=list[LeadOut])
async def leads(who: PlatformPrincipal = Depends(platform_user), session: AsyncSession = Depends(get_session)):
    stmt = select(Lead).order_by(Lead.next_step_on.is_(None), Lead.next_step_on, Lead.updated_at.desc())
    if not who.manager:
        stmt = stmt.where(or_(Lead.assigned_to == who.id, Lead.created_by == who.id))
    return await _lead_outs(session, list((await session.scalars(stmt)).all()))


@router.post("/leads", response_model=LeadOut, status_code=201)
async def lead_create(body: LeadIn, who: PlatformPrincipal = Depends(platform_user), session: AsyncSession = Depends(get_session)):
    data = _clean(body.model_dump())
    data["assigned_to"] = await _check_assignee(session, who, body.assigned_to)
    if data["status"] in ("trial", "paid") and not who.manager:
        data["status"] = "demo"
    lead = Lead(**data, created_by=who.id)
    session.add(lead)
    await session.commit()
    await session.refresh(lead)
    return await _lead_out(session, lead)


@router.patch("/leads/{lead_id}", response_model=LeadOut)
async def lead_update(lead_id: int, body: LeadPatch, who: PlatformPrincipal = Depends(platform_user),
                      session: AsyncSession = Depends(get_session)):
    lead = await _my_lead(session, who, lead_id)
    data = _clean(body.model_dump(exclude_unset=True, exclude={"clear_assignee", "clear_next_step_on", "clear_staff_count"}))
    if "assigned_to" in data:
        data["assigned_to"] = await _check_assignee(session, who, data["assigned_to"])
    if body.clear_assignee and who.manager:
        data["assigned_to"] = None
    if body.clear_next_step_on:
        data["next_step_on"] = None
    if body.clear_staff_count:
        data["staff_count"] = None
    if data.get("status") == "paid" and not who.manager:
        raise HTTPException(403, "To'lovni platforma egasi yoki sherigi belgilaydi")
    for key, value in data.items():
        if value is not None or key in ("assigned_to", "next_step_on", "staff_count"):
            setattr(lead, key, value)
    lead.updated_at = now_utc()
    await session.commit()
    await session.refresh(lead)
    return await _lead_out(session, lead)


def _same(name: str) -> str:
    return " ".join(name.lower().replace("ʻ", "'").replace("‘", "'").replace("`", "'").split())


@router.post("/leads/import", response_model=LeadImportOut)
async def lead_import(body: LeadImport, who: PlatformPrincipal = Depends(platform_user), session: AsyncSession = Depends(get_session)):
    """A list from a spreadsheet or the maps: a business already in the list (same name or phone) is skipped."""
    assignee = await _check_assignee(session, who, body.assigned_to)
    known = (await session.execute(select(Lead.name, Lead.phone))).all()
    digits = lambda v: "".join(ch for ch in v if ch.isdigit())  # noqa: E731
    names, phones = {_same(n) for n, _ in known}, {digits(p)[-9:] for _, p in known if len(digits(p)) >= 9}
    created = skipped = 0
    for item in body.leads:
        data = _clean(item.model_dump())
        phone = digits(data["phone"])
        if _same(data["name"]) in names or (len(phone) >= 9 and phone[-9:] in phones):
            skipped += 1
            continue
        names.add(_same(data["name"]))
        if len(phone) >= 9:
            phones.add(phone[-9:])
        if data["status"] in ("trial", "paid"):
            data["status"] = "new"
        data["assigned_to"] = assignee
        session.add(Lead(**data, created_by=who.id))
        created += 1
    await session.commit()
    return LeadImportOut(created=created, skipped=skipped)


@router.get("/leads/{lead_id}/notes", response_model=list[LeadNoteOut])
async def lead_notes(lead_id: int, who: PlatformPrincipal = Depends(platform_user), session: AsyncSession = Depends(get_session)):
    await _my_lead(session, who, lead_id)
    names = dict((await session.execute(select(PlatformUser.id, PlatformUser.name))).all())
    rows = (await session.scalars(select(LeadNote).where(LeadNote.lead_id == lead_id)
                                  .order_by(LeadNote.day.desc(), LeadNote.id.desc()))).all()
    return [LeadNoteOut.model_validate(n).model_copy(update={"user_name": names.get(n.user_id, "") if n.user_id else "Ega"}) for n in rows]


@router.post("/leads/{lead_id}/notes", response_model=LeadNoteOut, status_code=201)
async def lead_note_add(lead_id: int, body: LeadNoteIn, who: PlatformPrincipal = Depends(platform_user),
                        session: AsyncSession = Depends(get_session)):
    """What was said; the first conversation moves a new lead to "talked"."""
    lead = await _my_lead(session, who, lead_id)
    note = LeadNote(lead_id=lead.id, kind=body.kind, text=strip_controls(body.text).strip(), day=body.day or salon_today(), user_id=who.id)
    session.add(note)
    if lead.status in ("new", "later"):
        lead.status = "talked"
    lead.updated_at = now_utc()
    await session.commit()
    await session.refresh(note)
    return LeadNoteOut.model_validate(note).model_copy(update={"user_name": who.name if who.id else "Ega"})


# ---------- the team ----------

@router.get("/team", response_model=list[TeamOut])
async def team_list(who: PlatformPrincipal = Depends(platform_user), session: AsyncSession = Depends(get_session)):
    """Everyone sees who is on the team (to hand a lead over); only managers change it."""
    return (await session.scalars(select(PlatformUser).order_by(PlatformUser.is_active.desc(), PlatformUser.id))).all()


@router.post("/team", response_model=TeamOut, status_code=201)
async def team_add(body: TeamIn, who: PlatformPrincipal = Depends(platform_manager), session: AsyncSession = Depends(get_session)):
    if body.role == "partner" and who.role != "owner":
        raise HTTPException(403, "Sherikni faqat platforma egasi qo'shadi")
    if await session.scalar(select(PlatformUser.id).where(PlatformUser.login == body.login)):
        raise HTTPException(409, "Bu login band")
    user = PlatformUser(login=body.login, name=body.name.strip(), role=body.role, password_hash=hash_password(body.password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.patch("/team/{user_id}", response_model=TeamOut)
async def team_update(user_id: int, body: TeamPatch, who: PlatformPrincipal = Depends(platform_manager),
                      session: AsyncSession = Depends(get_session)):
    user = await session.get(PlatformUser, user_id)
    if user is None:
        raise HTTPException(404, "Xodim topilmadi")
    if (user.role == "partner" or body.role == "partner") and who.role != "owner":
        raise HTTPException(403, "Sherikni faqat platforma egasi boshqaradi")
    if body.name is not None:
        user.name = body.name.strip()
    if body.role is not None:
        user.role = body.role
    if body.password is not None:
        user.password_hash = hash_password(body.password)
        user.token_version += 1
    if body.is_active is not None:
        user.is_active = body.is_active
        user.token_version += 1
    await session.commit()
    await session.refresh(user)
    return user


# ---------- money and the big picture ----------

@router.get("/payments", response_model=list[PaymentOut])
async def payments(who: PlatformPrincipal = Depends(platform_manager), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(Payment, Salon.name).join(Salon, Salon.id == Payment.salon_id).order_by(Payment.paid_at.desc()).limit(200)
    )).all()
    return [PaymentOut.model_validate(p).model_copy(update={"salon_name": n}) for p, n in rows]


@router.get("/stats")
async def stats(who: PlatformPrincipal = Depends(platform_user), session: AsyncSession = Depends(get_session)) -> dict:
    lead_stmt = select(Lead.status, func.count()).group_by(Lead.status)
    if not who.manager:
        lead_stmt = lead_stmt.where(or_(Lead.assigned_to == who.id, Lead.created_by == who.id))
    by_status = {s: n for s, n in (await session.execute(lead_stmt)).all()}
    today = salon_today()
    due = select(func.count()).select_from(Lead).where(Lead.next_step_on.is_not(None), Lead.next_step_on <= today,
                                                       Lead.status.not_in(("paid", "lost")))
    if not who.manager:
        due = due.where(or_(Lead.assigned_to == who.id, Lead.created_by == who.id))
    out = {"leads": by_status, "due_today": await session.scalar(due) or 0,
           "demos": await session.scalar(select(func.count()).select_from(Salon).where(Salon.is_demo)) or 0}
    if who.manager:
        goal = await session.get(PlatformSetting, "goal")
        out["goal"] = int(goal.value) if goal else 0
        month_ago = now_utc() - timedelta(days=30)
        out["paying"] = await session.scalar(select(func.count()).select_from(Salon).where(
            Salon.is_demo.is_(False), Salon.is_active, Salon.paid_until >= today)) or 0
        out["income_30d"] = int(await session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.paid_at > month_ago)) or 0)
        out["income_total"] = int(await session.scalar(select(func.coalesce(func.sum(Payment.amount), 0))) or 0)
    return out


@router.put("/goal", response_model=GoalIn)
async def set_goal(body: GoalIn, who: PlatformPrincipal = Depends(platform_manager), session: AsyncSession = Depends(get_session)):
    """The money goal on the dashboard (0 hides it)."""
    row = await session.get(PlatformSetting, "goal")
    if row is None:
        session.add(PlatformSetting(key="goal", value=str(body.goal)))
    else:
        row.value = str(body.goal)
    await session.commit()
    return body
