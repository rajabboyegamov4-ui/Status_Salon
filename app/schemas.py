from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PhotoOut(ORM):
    id: str
    url: str
    caption: str = ""


class BranchOut(ORM):
    id: int
    name: str
    address: str
    landmark: str = ""
    hours_text: str = ""
    phone: str
    lat: float | None
    lng: float | None
    photo_url: str | None
    photos: list[PhotoOut] = []


class ServiceOut(BaseModel):
    id: int
    name: str
    description: str
    category: str
    price: int
    duration_min: int


class MasterOut(ORM):
    id: int
    name: str
    photo_url: str | None
    rank: str
    bio: str = ""
    experience_years: int | None = None
    specialties: list[str] = []
    instagram: str = ""
    works: list[PhotoOut] = []
    rating: float | None = None  # average of visible reviews, one decimal
    reviews_count: int = 0

    @field_validator("specialties", mode="before")
    @classmethod
    def _split(cls, v):
        return [x.strip() for x in v.split(",") if x.strip()] if isinstance(v, str) else v


class MasterCardOut(MasterOut):
    """A barber on the salon's front page: who they are and where they work."""
    branch_id: int
    branch_name: str


class MasterQuoteOut(MasterOut):
    price: int | None = None
    duration_min: int | None = None


class SlotOut(BaseModel):
    start: datetime
    master_ids: list[int]


class SlotsOut(BaseModel):
    date: date
    timezone: str
    duration_min: int | None
    slots: list[SlotOut]


class DateAvailability(BaseModel):
    date: date
    has_slots: bool


class BookingCreate(BaseModel):
    branch_id: int
    master_id: int | None = Field(None, description="null = istalgan bo'sh master")
    service_ids: list[int] = Field(min_length=1, max_length=10)
    starts_at: datetime
    comment: str = Field("", max_length=500)


class AdminBookingCreate(BookingCreate):
    client_phone: str = ""  # empty: a walk-in who gave no number
    client_name: str = ""


class BookingItemOut(ORM):
    service_id: int
    service_name: str
    price: int
    duration_min: int


class BookingOut(ORM):
    id: int
    review_rating: int | None = None
    branch_id: int
    branch_name: str
    master_id: int
    master_name: str
    starts_at: datetime
    ends_at: datetime
    total_price: int
    status: str
    items: list[BookingItemOut]

    @classmethod
    def of(cls, b) -> "BookingOut":
        return cls(
            id=b.id,
            branch_id=b.branch_id,
            branch_name=b.branch.name,
            master_id=b.master_id,
            master_name=b.master.name,
            starts_at=b.starts_at,
            ends_at=b.ends_at,
            total_price=b.total_price,
            status=b.status,
            items=[BookingItemOut.model_validate(i) for i in b.items],
            review_rating=b.review.rating if b.review else None,
        )


class AdminBookingOut(BookingOut):
    client_name: str
    client_phone: str
    source: str
    comment: str

    @classmethod
    def of(cls, b) -> "AdminBookingOut":
        base = BookingOut.of(b).model_dump()
        return cls(**base, client_name=b.user.name, client_phone=b.user.phone, source=b.source, comment=b.comment)


class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field("", max_length=1000)


class ReviewOut(BaseModel):
    id: int
    rating: int
    comment: str
    client_name: str  # first name and initial only: "Aziz K."
    services: str = ""
    created_at: datetime
    master_id: int | None = None
    master_name: str = ""
    is_hidden: bool = False


class ReviewVisibility(BaseModel):
    is_hidden: bool


class StatusUpdate(BaseModel):
    status: str = Field(pattern="^(completed|no_show|cancelled)$")


class CodeRequest(BaseModel):
    phone: str


class CodeVerify(BaseModel):
    phone: str
    code: str = Field(min_length=4, max_length=8)
    name: str | None = None


class UserOut(ORM):
    id: int
    phone: str
    name: str


# ---------- salon & staff ----------

LOGIN_PATTERN = r"^[a-z0-9._-]{3,40}$"


class SalonInfo(ORM):
    slug: str
    name: str
    phone: str
    paid_until: date
    active: bool = True
    description: str = ""
    instagram: str = ""
    cover_url: str | None = None
    logo_url: str | None = None
    telegram_bot: str = ""  # clients log in and get messages through this bot
    kind: str = "barber"  # see app/kinds.py
    modules: list[str] = ["booking"]  # booking, orders
    term: str = "Usta"  # what the specialist is called: Usta, Shifokor, Kosmetolog...
    client_word: str = "mijoz"  # mijoz | bemor
    sms_login: bool = False  # SMS codes are switched on for this server (fallback when there is no bot)


class SalonProfileUpdate(BaseModel):
    description: str | None = Field(None, max_length=2000)
    instagram: str | None = Field(None, max_length=60)
    phone: str | None = Field(None, max_length=20)


class BranchUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    address: str | None = Field(None, max_length=255)
    landmark: str | None = Field(None, max_length=255)
    hours_text: str | None = Field(None, max_length=120)
    phone: str | None = Field(None, max_length=20)
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)


class MasterProfileUpdate(BaseModel):
    rank: str | None = Field(None, max_length=40)
    bio: str | None = Field(None, max_length=1500)
    experience_years: int | None = Field(None, ge=0, le=60)
    specialties: list[str] | None = Field(None, max_length=12)
    instagram: str | None = Field(None, max_length=60)


class StaffOut(ORM):
    id: int
    login: str
    name: str
    role: str
    master_id: int | None
    is_active: bool
    last_login_at: datetime | None = None


class StaffMe(StaffOut):
    salon: SalonInfo
    branch_id: int | None = None  # a barber's own branch


class StaffLogin(BaseModel):
    login: str = Field(max_length=40)
    password: str = Field(max_length=128)


class TeamMemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    login: str = Field(pattern=LOGIN_PATTERN)
    password: str = Field(min_length=6, max_length=128)
    role: str = Field("barber", pattern="^(barber|admin)$")
    rank: str = Field("Master", max_length=40)
    branch_id: int | None = None


class TeamMemberUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    is_active: bool | None = None
    password: str | None = Field(None, min_length=6, max_length=128)


class PasswordChange(BaseModel):
    old_password: str = Field(max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


# ---------- platform ----------

class PlatformLogin(BaseModel):
    login: str = Field(max_length=40)
    password: str = Field(max_length=128)


def _kind(v: str) -> str:
    from app.kinds import KINDS
    if v not in KINDS:
        raise ValueError("Soha noto'g'ri")
    return v


class SalonCreate(BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field("", max_length=20)
    months: int = Field(1, ge=0, le=24)
    trial_days: int = Field(0, ge=0, le=31)  # free days on top of paid months (a demo gets 14)
    branch_name: str = Field("", max_length=120)
    branch_address: str = Field("", max_length=255)
    owner_name: str = Field(min_length=1, max_length=120)
    owner_login: str = Field(pattern=LOGIN_PATTERN)
    owner_password: str = Field(min_length=6, max_length=128)
    kind: str = "barber"  # one of app/kinds.py: picks the starting services and the words used
    sample_masters: bool = False  # demo salons: three sample masters (logins = their names, owner's password)
    is_demo: bool = False
    lead_id: int | None = None  # the lead this business came from

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        return _kind(v)


class SalonUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    phone: str | None = Field(None, max_length=20)
    paid_until: date | None = None
    extend_months: int | None = Field(None, ge=1, le=24)
    amount: int | None = Field(None, ge=0, le=1_000_000_000)  # so'm received for extend_months
    is_active: bool | None = None
    is_demo: bool | None = None
    modules: list[Literal["booking", "orders"]] | None = Field(None, min_length=1, max_length=2)


class PlatformSalonOut(ORM):
    id: int
    slug: str
    name: str
    phone: str
    plan: str
    kind: str = "barber"
    paid_until: date
    is_active: bool
    active: bool
    is_demo: bool = False
    modules: list[str] = ["booking"]
    telegram_bot: str = ""
    created_at: datetime
    staff_count: int = 0
    bookings_30d: int = 0
    paid_total: int | None = None  # managers only
    lead_id: int | None = None


class TakeBot(BaseModel):
    from_salon_id: int


class PlatformMe(BaseModel):
    id: int | None
    name: str
    role: str


class TeamIn(BaseModel):
    login: str = Field(pattern=LOGIN_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["partner", "sales"] = "sales"


class TeamPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    password: str | None = Field(None, min_length=8, max_length=128)
    role: Literal["partner", "sales"] | None = None
    is_active: bool | None = None


class TeamOut(ORM):
    id: int
    login: str
    name: str
    role: str
    is_active: bool
    last_login_at: datetime | None = None


LeadStatus = Literal["new", "talked", "demo", "trial", "paid", "lost"]


class LeadIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = "other"
    phone: str = Field("", max_length=20)
    contact: str = Field("", max_length=120)
    address: str = Field("", max_length=255)
    status: LeadStatus = "new"
    assigned_to: int | None = None
    next_step_on: date | None = None
    next_step: str = Field("", max_length=200)
    note: str = Field("", max_length=4000)

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        return _kind(v)


class LeadPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    kind: str | None = None
    phone: str | None = Field(None, max_length=20)
    contact: str | None = Field(None, max_length=120)
    address: str | None = Field(None, max_length=255)
    status: LeadStatus | None = None
    assigned_to: int | None = None
    clear_assignee: bool = False  # assigned_to=None means "unchanged"; this hands the lead back to the owner
    next_step_on: date | None = None
    clear_next_step_on: bool = False
    next_step: str | None = Field(None, max_length=200)
    note: str | None = Field(None, max_length=4000)

    @field_validator("kind")
    @classmethod
    def _known(cls, v: str | None) -> str | None:
        return None if v is None else _kind(v)


class LeadOut(ORM):
    id: int
    name: str
    kind: str
    phone: str
    contact: str
    address: str
    status: str
    assigned_to: int | None
    assigned_name: str = ""
    salon_id: int | None
    salon_slug: str = ""
    next_step_on: date | None
    next_step: str
    note: str
    created_at: datetime
    updated_at: datetime


class PaymentOut(ORM):
    id: int
    salon_id: int
    salon_name: str = ""
    amount: int
    months: int
    note: str
    paid_at: datetime
