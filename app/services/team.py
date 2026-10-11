"""Creating salons, branches and staff accounts (used by the platform, the salon owner and the seed)."""

from datetime import date, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, BranchService, Master, MasterService, Salon, Service, Staff, WorkingHours
from app.security import hash_password

# A new salon starts with the services of its kind; the owner adjusts names and prices later.
TEMPLATES = {
    # name, category, price (so'm), duration (min)
    "barber": [
        ("Soch olish", "Soch", 120_000, 30),
        ("Soqol olish", "Soqol", 80_000, 20),
        ("Soch + soqol", "Kompleks", 180_000, 50),
        ("Kompleks (soch, soqol, yuz parvarishi)", "Kompleks", 300_000, 90),
    ],
    "beauty": [
        ("Soch turmagi (ukladka)", "Soch", 150_000, 60),
        ("Soch bo'yash", "Soch", 400_000, 150),
        ("Manikyur + gel lak", "Tirnoq", 150_000, 90),
        ("Pedikyur", "Tirnoq", 180_000, 75),
        ("Qosh va kiprik dizayni", "Qosh va kiprik", 100_000, 45),
        ("Kechki makiyaj", "Makiyaj", 300_000, 60),
    ],
    "cosmetology": [
        ("Yuzni tozalash", "Yuz", 300_000, 60),
        ("Kimyoviy piling", "Yuz", 350_000, 45),
        ("Yuz massaji", "Yuz", 200_000, 40),
        ("Kiprik laminatsiyasi", "Qosh va kiprik", 200_000, 60),
        ("Lazer epilyatsiya (bir zona)", "Epilyatsiya", 250_000, 30),
        ("Kosmetolog konsultatsiyasi", "Konsultatsiya", 100_000, 20),
    ],
}
DEFAULT_SERVICES = TEMPLATES["barber"]
# Demo salons get three sample masters so a presentation starts with a full schedule.
SAMPLE_MASTERS = {
    "barber": [("Jasur", "Top-master", "Fade, Soqol dizayni"), ("Bekzod", "Master", "Klassik qirqim"), ("Sardor", "Master", "Crop, Bolalar")],
    "beauty": [("Dilnoza", "Top-master", "Soch bo'yash, Ukladka"), ("Madina", "Master", "Manikyur, Pedikyur"), ("Sevara", "Master", "Makiyaj, Qosh dizayni")],
    "cosmetology": [("Nigora", "Top-master", "Yuz tozalash, Piling"), ("Kamola", "Master", "Kiprik laminatsiyasi"), ("Zarina", "Master", "Lazer epilyatsiya")],
}
LOGIN_SAFE = str.maketrans({"'": "", "`": "", "ʻ": "", "ʼ": ""})
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
    kind: str = "barber",
    sample_masters: bool = False,
) -> Salon:
    if await session.scalar(select(Salon.id).where(Salon.slug == slug)):
        raise TeamError("Bu manzil band. Boshqa nom tanlang.")
    template = TEMPLATES.get(kind)
    if template is None:
        raise TeamError("Soha noto'g'ri")
    salon = Salon(slug=slug, name=name, phone=phone, paid_until=paid_until, kind=kind)
    session.add(salon)
    await session.flush()
    branch = Branch(salon_id=salon.id, name=branch_name or name, address=branch_address, phone=phone)
    services = [Service(salon_id=salon.id, name=n, category=c) for n, c, _, _ in template]
    session.add(branch)
    session.add_all(services)
    await session.flush()
    for s, (_, _, price, dur) in zip(services, template):
        session.add(BranchService(branch_id=branch.id, service_id=s.id, price=price, duration_min=dur))
    session.add(Staff(salon_id=salon.id, login=owner_login.lower(), password_hash=hash_password(owner_password),
                      name=owner_name, role="owner"))
    await session.flush()
    if sample_masters:
        for name_, rank, specialties in SAMPLE_MASTERS[kind]:
            login = name_.lower().translate(LOGIN_SAFE)
            if login == owner_login.lower():
                login += "1"
            member = await create_member(session, salon, name=name_, login=login, password=owner_password, rank=rank,
                                         price_factor=1.2 if rank == "Top-master" else None)
            master = await session.get(Master, member.master_id)
            master.specialties = specialties
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
