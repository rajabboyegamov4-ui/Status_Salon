"""Fill an empty database with a demo salon "premium": a branch, services, three barbers and their logins.

Usage: python -m scripts.seed
Logins (change the passwords after the first login):
  owner  / owner123   salon owner     -> /premium/admin
  malika / admin123   administrator   -> /premium/admin
  jasur, bekzod, sardor / barber123   -> /premium/panel
"""

import asyncio
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Branch, Master, Salon
from app.services import team
from app.tenancy import salon_today

DEMO_PASSWORDS = {"owner": "owner123", "admin": "admin123", "barber": "barber123"}
BARBERS = [("Jasur", "jasur", "Top-master", 1.2), ("Bekzod", "bekzod", "Master", None), ("Sardor", "sardor", "Master", None)]
PROFILES = {
    "jasur": (9, "Fade, Soqol dizayni, Klassika", "Toshkent va Istanbul akademiyalarida o'qigan. Erkaklar uchun zamonaviy fade va aniq soqol konturi."),
    "bekzod": (5, "Klassik qirqim, Qaychi bilan", "Biznes uslubidagi klassik qirqimlar. Shoshilmasdan, toza ishlaydi."),
    "sardor": (3, "Crop, Teksturali soch, Bolalar", "Yoshlar uslubi: crop, tekstura, chiziqlar. Bolalar bilan ham yaxshi chiqishadi."),
}


async def seed_salon(session: AsyncSession, slug: str = "premium", name: str = "Premium Barber",
                     branch_name: str = "Premium Barber, Chilonzor") -> Salon:
    salon = await team.create_salon(
        session, slug=slug, name=name, phone="+998712000000", paid_until=salon_today() + timedelta(days=30),
        branch_name=branch_name, branch_address="Toshkent, Chilonzor 9-kvartal",
        owner_name="Salon egasi", owner_login="owner", owner_password=DEMO_PASSWORDS["owner"],
    )
    await team.create_member(session, salon, name="Malika", login="malika", password=DEMO_PASSWORDS["admin"], role="admin")
    for display, login, rank, factor in BARBERS:  # the top master charges 20% more
        member = await team.create_member(session, salon, name=display, login=login, password=DEMO_PASSWORDS["barber"],
                                          rank=rank, price_factor=factor)
        years, specialties, bio = PROFILES[login]
        master = await session.get(Master, member.master_id)
        master.experience_years, master.specialties, master.bio = years, specialties, bio
    branch = await session.scalar(select(Branch).where(Branch.salon_id == salon.id))
    branch.landmark, branch.lat, branch.lng = "Chilonzor metrosi yonida, Korzinka ro'parasida", 41.2756, 69.2034
    salon.description = "Erkaklar uchun premium barbershop: qahva, issiq sochiq va shoshilmasdan ishlaydigan ustalar."
    salon.instagram = "premiumbarber.uz"
    await session.commit()
    return salon


async def main() -> None:
    async with SessionLocal() as session:
        if await session.scalar(select(Salon.id).limit(1)):
            raise SystemExit("Database already has data; seed skipped.")
        await seed_salon(session)
    print("Seed done: /premium, /premium/panel, /premium/admin")


if __name__ == "__main__":
    asyncio.run(main())
