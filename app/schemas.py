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
    kind: str = "barber"  # barber | beauty | cosmetology: wording and examples on the site
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


class SalonCreate(BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field("", max_length=20)
    months: int = Field(1, ge=0, le=24)
    branch_name: str = Field("", max_length=120)
    branch_address: str = Field("", max_length=255)
    owner_name: str = Field(min_length=1, max_length=120)
    owner_login: str = Field(pattern=LOGIN_PATTERN)
    owner_password: str = Field(min_length=6, max_length=128)
    kind: Literal["barber", "beauty", "cosmetology"] = "barber"  # picks the starting services
    sample_masters: bool = False  # demo salons: three sample masters (logins = their names, owner's password)


class SalonUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    phone: str | None = Field(None, max_length=20)
    paid_until: date | None = None
    extend_months: int | None = Field(None, ge=1, le=24)
    is_active: bool | None = None


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
    created_at: datetime
    staff_count: int = 0
    bookings_30d: int = 0
