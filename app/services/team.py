"""Creating salons, branches and staff accounts (used by the platform, the salon owner and the seed)."""

from datetime import date, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, BranchService, Master, MasterService, Salon, Service, Staff, WorkingHours
from app.security import hash_password

# A new salon starts with these services; the owner adjusts prices later.
DEFAULT_SERVICES = [
    # name, category, price (so'm), duration (min)
    ("Soch olish", "Soch", 120_000, 30),
    ("Soqol olish", "Soqol", 80_000, 20),
    ("Soch + soqol", "Kompleks", 180_000, 50),
    ("Kompleks (soch, soqol, yuz parvarishi)", "Kompleks", 300_000, 90),
]
DEFAULT_HOURS = (time(10), time(21))


class TeamError(Exception):
    pass


async def create_salon(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    paid_until: date,
    phone: str = "",
    branch_name: str = "",
    branch_address: str = "",
    owner_name: str,
    owner_login: str,
    owner_password: str,
) -> Salon:
    if await session.scalar(select(Salon.id).where(Salon.slug == slug)):
        raise TeamError("Bu manzil band. Boshqa nom tanlang.")
    salon = Salon(slug=slug, name=name, phone=phone, paid_until=paid_until)
    session.add(salon)
    await session.flush()
    branch = Branch(salon_id=salon.id, name=branch_name or name, address=branch_address, phone=phone)
    services = [Service(salon_id=salon.id, name=n, category=c) for n, c, _, _ in DEFAULT_SERVICES]
    session.add(branch)
    session.add_all(services)
    await session.flush()
    for s, (_, _, price, dur) in zip(services, DEFAULT_SERVICES):
        session.add(BranchService(branch_id=branch.id, service_id=s.id, price=price, duration_min=dur))
    session.add(Staff(salon_id=salon.id, login=owner_login.lower(), password_hash=hash_password(owner_password),
                      name=owner_name, role="owner"))
    await session.flush()
    return salon


async def create_member(
    session: AsyncSession,
    salon: Salon,
    *,
    name: str,
    login: str,
    password: str,
    role: str = "barber",
    rank: str = "Master",
    branch_id: int | None = None,
    price_factor: float | None = None,
) -> Staff:
    """An admin account, or a barber: a master in the schedule plus their own panel login."""
    login = login.strip().lower()
    if await session.scalar(select(Staff.id).where(Staff.salon_id == salon.id, Staff.login == login)):
        raise TeamError("Bu login band. Boshqasini tanlang.")
    master_id = None
    if role == "barber":
        stmt = select(Branch).where(Branch.salon_id == salon.id, Branch.is_active).order_by(Branch.id)
        if branch_id is not None:
            stmt = stmt.where(Branch.id == branch_id)
        branch = await session.scalar(stmt.limit(1))
        if branch is None:
            raise TeamError("Filial topilmadi")
        master = Master(branch_id=branch.id, name=name, rank=rank)
        session.add(master)
        await session.flush()
        offered = (
            await session.execute(
                select(BranchService.service_id, BranchService.price).where(BranchService.branch_id == branch.id)
            )
        ).all()
        for service_id, price in offered:
            override = int(price * price_factor) if price_factor else None
            session.add(MasterService(master_id=master.id, service_id=service_id, price_override=override))
        for weekday in range(7):
            session.add(WorkingHours(master_id=master.id, weekday=weekday, start_time=DEFAULT_HOURS[0], end_time=DEFAULT_HOURS[1]))
        master_id = master.id
    staff = Staff(salon_id=salon.id, login=login, password_hash=hash_password(password), name=name, role=role,
                  master_id=master_id)
    session.add(staff)
    await session.flush()
    return staff
