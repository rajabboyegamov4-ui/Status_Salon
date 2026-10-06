# Salon bron tizimi

FastAPI + PostgreSQL. Mijoz salonning Telegram boti orqali raqamini ulashib kiradi, bo'sh vaqtni tanlaydi va bron qiladi.
Bir masterga bir vaqtda ikki bron tushishi baza darajasida taqiqlangan (`bookings_no_overlap` EXCLUDE constraint).

## Ishga tushirish (lokal)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # DATABASE_URL ni to'g'rilang, kompyuterda APP_ENV=development
alembic upgrade head          # jadvallarni yaratadi
python -m scripts.seed        # "premium" demo saloni: filial, xizmatlar, 3 barber, loginlar
uvicorn app.main:app --reload # http://localhost:8000/docs
```

`SMS_BACKEND=console` bo'lsa SMS kodlar server logiga chiqadi; haqiqiy SMS uchun `SMS_BACKEND=eskiz`
va Eskiz.uz email/parolini kiriting. Eskiz yangi akkauntda faqat test matnlarini yuboradi;
haqiqiy matnlar uchun ular shablonni tasdiqlashi kerak.

## Ko'p salon (oylik obuna)

Bitta server ko'p salonga xizmat qiladi. Har salonning ma'lumoti alohida: mijozlar, barberlar,
bronlar, xizmatlar boshqa salonga ko'rinmaydi.

| Manzil | Kim uchun | Qanday kiradi |
| --- | --- | --- |
| `/premium` | Mijoz | Telegram bot (raqam ulashish) |
| `/premium/panel` | Barber (faqat o'z bronlari) | O'z login + paroli |
| `/premium/admin` | Salon egasi, administrator | O'z login + paroli |
| `/platform` | Platforma egasi (obunalar) | `PLATFORM_LOGIN` + parol |

- Yangi salonni `/platform` dan ulaysiz: manzil (masalan `navruz`), egasining login-paroli, necha oy to'langani.
- Salon egasi `/navruz/admin` → "Xodimlar" bo'limida har barberga login va parol beradi.
- Obuna muddati tugasa: mijoz sayti bron qabul qilmaydi, SMS yuborilmaydi, panel faqat ko'rish rejimida.
  Platformadan "+1 oy" bosilsa darhol tiklanadi.
- 5 marta noto'g'ri parol — login 15 daqiqaga bloklanadi.
- Profillar: salon muqovasi, filial rasmlari va xaritadagi joyi (Google/Yandex havolasidan), har bir ustaning yuzi,
  tajribasi, yo'nalishlari va ishlari rasmlari. Barber o'z profilini `/premium/panel` → "Profilim" da to'ldiradi.
  Rasmlar brauzerda kichraytirilib Postgres'da saqlanadi va `/media/{id}` dan beriladi.
- Baholar: mijoz "Keldi" deb belgilangan tashrifiga bir marta 1-5 yulduz va izoh qoldiradi ("Bronlarim").
  Rahbar haqoratli yoki reklama otzivni yashira oladi, yulduzlarni o'zgartira olmaydi.

Demo loginlar (`scripts.seed`): `owner / owner123`, `malika / admin123`, `jasur`, `bekzod`, `sardor / barber123`.
Ishga tushirgandan keyin parollarni almashtiring.

## Testlar

```bash
createdb salon_test   # bo'sh test bazasi
pytest -q
```

Testlar ichida: salonlar bir-birining ma'lumotini ko'rmasligi (`tests/test_tenancy.py`), 20 ta mijoz bir vaqtda bitta slotga bron qilsa, faqat 1 tasi o'tadi (`test_twenty_clients_race_for_one_slot`).

## Railway

1. Railway loyihasida **Postgres** qo'shing va web servisni GitHub repodan yarating.
2. Web servis o'zgaruvchilari:
   - `DATABASE_URL=${{Postgres.DATABASE_URL}}` (driver avtomatik `asyncpg` ga o'giriladi)
   - `APP_ENV=production`
   - `JWT_SECRET`: 48+ belgili tasodifiy satr (`python -c "import secrets; print(secrets.token_urlsafe(48))"`)
   - `TOKEN_KEY`: alohida tasodifiy satr. Keyin almashtirmang: almashsa barcha botlarni qayta ulash kerak bo'ladi.
   - `TELEGRAM_BACKEND=live`, `SMS_BACKEND=none`
   - `PUBLIC_URL=https://<domen>`: aniq ochiq manzil. Domen almashsa salonlar botni qayta ulaydi.
   - `PLATFORM_LOGIN`, `PLATFORM_PASSWORD_HASH` (`python -m scripts.hash_password` chiqargan satr)
3. Railway servis sozlamalari (Settings > Deploy): Pre-deploy `alembic upgrade head`, Start `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*'`, Healthcheck `/api/health`, 1 nusxa (rate-limit va eslatma ishchisi bitta jarayonga mo'ljallangan).
4. Productionda `scripts.seed` ni ishga tushirmang: u ma'lum parollar bilan demo loginlar yaratadi. Salonlar `/platform` orqali qo'shiladi.

## Tuzilma

| Fayl | Vazifasi |
| --- | --- |
| `app/models.py` | Jadvallar, double booking constraint |
| `app/scheduling.py` | Bo'sh vaqt oraliqlari hisobi (sof funksiyalar) |
| `app/services/availability.py` | Master grafigi, bronlar va slotlar |
| `app/services/booking.py` | Bron yaratish (advisory lock + qayta tekshiruv + constraint), bekor qilish |
| `app/auth.py` | Mijoz SMS kodi, xodim login + paroli (bloklash bilan), platforma admini |
| `app/tenancy.py` | Salonni manzildan topish, obuna tekshiruvi |
| `app/services/team.py` | Salon va xodim (barber) yaratish |
| `app/routers/schedule.py` | Ish vaqti, dam olish/tanaffus, xizmat va narxlar, bronni ko'chirish, "hozir" uchun slotlar |
| `app/telegram.py`, `app/routers/telegram.py` | Salon boti: ulash, kirish, webhook tugmalari |
| `app/notifications.py` | Bot (zaxira: SMS) xabarlari, eslatma ishchisi (har daqiqa) |
| `app/routers/` | API: `/api/s/{salon}/...` (mijoz), `/api/s/{salon}/staff/...` (xodim), `/api/platform/...` |

API hujjati server ishlaganda `/docs` sahifasida.
