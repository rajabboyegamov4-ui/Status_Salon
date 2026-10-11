"""Who is calling: salon clients (phone + SMS code), salon staff (login + password), platform admin."""

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import OtpCode, Salon, Staff, User
from app.ratelimit import Window
from app.security import decode_token, issue_token, verify_password
from app.services.availability import now_utc
from app.tenancy import get_salon
from app.text import clean_name

CLIENT_COOKIE = "client_session"
STAFF_COOKIE = "staff_session"
PLATFORM_COOKIE = "platform_session"
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def normalize_phone(raw: str) -> str:
    """Accepts '90 123 45 67', '+998901234567', '998901234567' -> '+998901234567'."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) != 12 or not digits.startswith("998"):
        raise AuthError("Telefon raqam noto'g'ri. Namuna: +998 90 123 45 67")
    return "+" + digits


def _token(request: Request, cookie: str) -> dict | None:
    token = request.cookies.get(cookie)
    if not token:
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else None
    return decode_token(token) if token else None


def cookie_path(salon: Salon | None) -> str:
    """Salon sessions are scoped to that salon's API, so one browser can be signed in to several salons."""
    return f"/api/s/{salon.slug}" if salon else "/api/platform"


def set_cookie(response, name: str, token: str, path: str, days: int | None = None) -> None:
    response.set_cookie(
        name, token, max_age=(days or settings.jwt_ttl_days) * 86400, httponly=True,
        secure=settings.cookie_secure, samesite="lax", path=path,
    )


def clear_cookie(response, name: str, path: str) -> None:
    response.delete_cookie(name, path=path, httponly=True, secure=settings.cookie_secure, samesite="lax")


# ---------- clients: phone + SMS code, per salon ----------

def _hash_code(salon_id: int, phone: str, code: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), f"{salon_id}:{phone}:{code}".encode(), hashlib.sha256).hexdigest()


OTP_LOCK_NS = 7302  # advisory lock namespace: one code request per phone at a time


async def request_code(session: AsyncSession, salon: Salon, phone: str) -> str:
    now = now_utc()
    await session.execute(select(func.pg_advisory_xact_lock(OTP_LOCK_NS, func.hashtext(f"{salon.id}:{phone}"))))
    count, last = (
        await session.execute(
            select(func.count(), func.max(OtpCode.created_at)).where(
                OtpCode.salon_id == salon.id, OtpCode.phone == phone, OtpCode.created_at > now - timedelta(hours=1)
            )
        )
    ).one()
    if count >= settings.otp_max_per_hour:
        raise AuthError("Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring.", 429)
    if last and now - last < timedelta(seconds=settings.otp_resend_cooldown_sec):
        raise AuthError("Kod yaqinda yuborildi. Bir daqiqadan keyin qayta so'rang.", 429)
    code = f"{secrets.randbelow(1_000_000):06d}"
    session.add(OtpCode(salon_id=salon.id, phone=phone, code_hash=_hash_code(salon.id, phone, code),
                        expires_at=now + timedelta(minutes=settings.otp_ttl_min), created_at=now))
    await session.commit()
    return code


async def verify_code(session: AsyncSession, salon: Salon, phone: str, code: str, name: str | None) -> User:
    now = now_utc()
    otp = await session.scalar(
        select(OtpCode)
        .where(OtpCode.salon_id == salon.id, OtpCode.phone == phone, OtpCode.consumed_at.is_(None), OtpCode.expires_at > now)
        .order_by(OtpCode.created_at.desc()).limit(1).with_for_update()
    )
    if otp is None:
        raise AuthError("Kod eskirgan yoki topilmadi. Yangi kod so'rang.")
    if otp.attempts >= settings.otp_max_attempts:
        raise AuthError("Urinishlar soni tugadi. Yangi kod so'rang.", 429)
    if not hmac.compare_digest(otp.code_hash, _hash_code(salon.id, phone, code.strip())):
        otp.attempts += 1
        await session.commit()
        raise AuthError("Kod noto'g'ri")
    otp.consumed_at = now
    user = await session.scalar(select(User).where(User.salon_id == salon.id, User.phone == phone))
    if user is None:
        user = User(salon_id=salon.id, phone=phone, name=clean_name(name))
        session.add(user)
    elif clean_name(name) and not user.name:
        user.name = clean_name(name)
    await session.commit()
    return user


async def current_client(
    request: Request, salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)
) -> User:
    data = _token(request, CLIENT_COOKIE)
    if not data or data.get("kind") != "client":
        raise HTTPException(401, "Avval tizimga kiring")
    if data.get("salon") != salon.id:  # a session from another salon is not valid here
        raise HTTPException(401, "Avval tizimga kiring")
    user = await session.get(User, int(data["sub"]))
    if user is None or user.salon_id != salon.id:
        raise HTTPException(401, "Avval tizimga kiring")
    return user


# ---------- staff: login + password, per salon ----------

async def staff_login(session: AsyncSession, salon: Salon, login: str, password: str) -> Staff:
    now = now_utc()
    staff = await session.scalar(
        select(Staff).where(Staff.salon_id == salon.id, Staff.login == login.strip().lower()).with_for_update()
    )
    if staff is None or not staff.is_active:
        verify_password(password, "scrypt$AAAAAAAAAAAAAAAAAAAAAA==$AAAA")  # same cost whether or not the login exists
        raise AuthError("Login yoki parol noto'g'ri", 401)
    if staff.locked_until and staff.locked_until > now:
        minutes = int((staff.locked_until - now).total_seconds() // 60) + 1
        raise AuthError(f"Ko'p xato urinish. {minutes} daqiqadan keyin qayta urinib ko'ring.", 429)
    if not verify_password(password, staff.password_hash):
        staff.failed_logins += 1
        if staff.failed_logins >= MAX_FAILED_LOGINS:
            staff.failed_logins = 0
            staff.locked_until = now + timedelta(minutes=LOCK_MINUTES)
        await session.commit()
        raise AuthError("Login yoki parol noto'g'ri", 401)
    staff.failed_logins = 0
    staff.locked_until = None
    staff.last_login_at = now
    await session.commit()
    return staff


async def current_staff(
    request: Request, salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)
) -> Staff:
    data = _token(request, STAFF_COOKIE)
    if not data or data.get("kind") != "staff" or data.get("salon") != salon.id:
        raise HTTPException(401, "Panelga kiring")
    staff = await session.get(Staff, int(data["sub"]))
    if staff is None or not staff.is_active or staff.salon_id != salon.id:
        raise HTTPException(401, "Panelga kiring")
    if data.get("ver", 0) != staff.token_version:  # password changed since this session started
        raise HTTPException(401, "Parol o'zgargan. Qaytadan kiring.")
    return staff


def staff_token(staff: Staff) -> str:
    return issue_token("staff", staff.id, staff.salon_id, version=staff.token_version)


async def manager(staff: Staff = Depends(current_staff)) -> Staff:
    """Owner or administrator: sees every master, books for clients, manages the team."""
    if staff.role not in ("owner", "admin"):
        raise HTTPException(403, "Bu amal faqat salon rahbari yoki administrator uchun")
    return staff


# ---------- platform admin (the business selling subscriptions) ----------

PLATFORM_FAILS = Window(MAX_FAILED_LOGINS, LOCK_MINUTES * 60)


def platform_login(login: str, password: str, ip: str) -> str:
    if not settings.platform_login or not settings.platform_password_hash:
        raise AuthError("Platforma admini sozlanmagan", 503)
    if PLATFORM_FAILS.blocked(ip):
        raise AuthError(f"Ko'p xato urinish. {LOCK_MINUTES} daqiqadan keyin qayta urinib ko'ring.", 429)
    ok_login = hmac.compare_digest(login.strip().lower(), settings.platform_login.lower())
    ok_pass = verify_password(password, settings.platform_password_hash)
    if not (ok_login and ok_pass):
        PLATFORM_FAILS.hit(ip)
        raise AuthError("Login yoki parol noto'g'ri", 401)
    PLATFORM_FAILS.clear(ip)
    return issue_token("platform", 0, None, days=1)


async def platform_admin(request: Request) -> None:
    data = _token(request, PLATFORM_COOKIE)
    if not data or data.get("kind") != "platform":
        raise HTTPException(401, "Platforma paneliga kiring")
