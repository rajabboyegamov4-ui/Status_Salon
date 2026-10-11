"""Kinds of business the platform serves: what a new business starts with and what its people are called.

The same booking system runs a barbershop, a dental clinic or a car service; only the words, the starting
services and the sample team differ. The owner changes services and prices in the admin panel afterwards.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Kind:
    label: str  # shown on the platform and the presentation page
    term: str  # what the specialist is called: "Usta", "Shifokor", "Kosmetolog"
    client: str  # "mijoz" or "bemor"
    services: tuple[tuple[str, str, int, int], ...]  # name, category, price (so'm), duration (min)
    sample: tuple[tuple[str, str, str], ...]  # demo team: name, rank, specialties


KINDS: dict[str, Kind] = {
    "barber": Kind("Erkaklar sartaroshxonasi", "Usta", "mijoz", (
        ("Soch olish", "Soch", 120_000, 30),
        ("Soqol olish", "Soqol", 80_000, 20),
        ("Soch + soqol", "Kompleks", 180_000, 50),
        ("Kompleks (soch, soqol, yuz parvarishi)", "Kompleks", 300_000, 90),
    ), (("Jasur", "Top-master", "Fade, Soqol dizayni"), ("Bekzod", "Master", "Klassik qirqim"), ("Sardor", "Master", "Crop, Bolalar"))),
    "beauty": Kind("Ayollar go'zallik saloni", "Usta", "mijoz", (
        ("Soch turmagi (ukladka)", "Soch", 150_000, 60),
        ("Soch bo'yash", "Soch", 400_000, 150),
        ("Manikyur + gel lak", "Tirnoq", 150_000, 90),
        ("Pedikyur", "Tirnoq", 180_000, 75),
        ("Qosh va kiprik dizayni", "Qosh va kiprik", 100_000, 45),
        ("Kechki makiyaj", "Makiyaj", 300_000, 60),
    ), (("Dilnoza", "Top-master", "Soch bo'yash, Ukladka"), ("Madina", "Master", "Manikyur, Pedikyur"), ("Sevara", "Master", "Makiyaj, Qosh dizayni"))),
    "cosmetology": Kind("Kosmetologiya", "Kosmetolog", "mijoz", (
        ("Yuzni tozalash", "Yuz", 300_000, 60),
        ("Kimyoviy piling", "Yuz", 350_000, 45),
        ("Yuz massaji", "Yuz", 200_000, 40),
        ("Kiprik laminatsiyasi", "Qosh va kiprik", 200_000, 60),
        ("Lazer epilyatsiya (bir zona)", "Epilyatsiya", 250_000, 30),
        ("Kosmetolog konsultatsiyasi", "Konsultatsiya", 100_000, 20),
    ), (("Nigora", "Top-master", "Yuz tozalash, Piling"), ("Kamola", "Master", "Kiprik laminatsiyasi"), ("Zarina", "Master", "Lazer epilyatsiya"))),
    "dental": Kind("Stomatologiya", "Shifokor", "bemor", (
        ("Ko'rik va maslahat", "Ko'rik", 50_000, 30),
        ("Professional tish tozalash", "Gigiyena", 250_000, 60),
        ("Plomba qo'yish", "Davolash", 350_000, 60),
        ("Tish oqartirish", "Estetika", 900_000, 90),
        ("Implant bo'yicha maslahat", "Ko'rik", 0, 30),
    ), (("Dr. Aziza Karimova", "Top-master", "Terapiya, Estetika"), ("Dr. Sardor Aliyev", "Master", "Implantologiya"),
        ("Dr. Malika Yusupova", "Master", "Bolalar stomatologiyasi"))),
    "clinic": Kind("Ko'p tarmoqli klinika", "Shifokor", "bemor", (
        ("Terapevt qabuli", "Qabul", 150_000, 30),
        ("Kardiolog qabuli", "Qabul", 200_000, 30),
        ("Nevrolog qabuli", "Qabul", 200_000, 30),
        ("EKG", "Tekshiruv", 80_000, 30),
        ("UZI tekshiruvi", "Tekshiruv", 180_000, 30),
    ), (("Dr. Bekzod Rahimov", "Top-master", "Terapevt"), ("Dr. Nilufar Saidova", "Master", "Kardiolog"),
        ("Dr. Jasur Toshmatov", "Master", "Nevrolog"))),
    "spa": Kind("SPA va massaj", "Massajchi", "mijoz", (
        ("Klassik massaj", "Massaj", 250_000, 60),
        ("Bosh va yelka massaji", "Massaj", 150_000, 30),
        ("Butun tana massaji", "Massaj", 350_000, 90),
        ("Hammom va massaj", "SPA", 450_000, 120),
    ), (("Rustam", "Top-master", "Klassik, Sport massaji"), ("Nodira", "Master", "Relaks, SPA"), ("Azamat", "Master", "Hammom"))),
    "auto": Kind("Avtoservis", "Usta", "mijoz", (
        ("Moy almashtirish", "Texnik xizmat", 150_000, 30),
        ("Kompyuter diagnostika", "Diagnostika", 200_000, 60),
        ("Tormoz kolodkalari", "Ta'mirlash", 250_000, 60),
        ("Shina almashtirish", "Shina", 100_000, 30),
    ), (("Akmal", "Top-master", "1-post, Dvigatel"), ("Farrux", "Master", "2-post, Yurish qismi"), ("Doniyor", "Master", "3-post, Shina"))),
    "vet": Kind("Veterinariya", "Veterinar", "mijoz", (
        ("Ko'rik", "Ko'rik", 100_000, 30),
        ("Emlash", "Profilaktika", 150_000, 30),
        ("Gruming", "Parvarish", 200_000, 60),
        ("UZI tekshiruvi", "Tekshiruv", 180_000, 30),
    ), (("Dr. Anvar Qodirov", "Top-master", "Terapiya, Jarrohlik"), ("Dr. Zarina Hamidova", "Master", "Emlash, Gruming"))),
    "other": Kind("Boshqa biznes", "Mutaxassis", "mijoz", (
        ("Konsultatsiya", "Asosiy", 100_000, 30),
        ("Asosiy xizmat", "Asosiy", 200_000, 60),
        ("Premium xizmat", "Premium", 400_000, 90),
    ), (("Aziz", "Top-master", ""), ("Laylo", "Master", ""), ("Timur", "Master", ""))),
}
DEFAULT_KIND = "barber"


def kind_of(key: str | None) -> Kind:
    return KINDS.get(key or "", KINDS[DEFAULT_KIND])
