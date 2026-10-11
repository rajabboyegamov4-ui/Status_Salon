from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSTZRANGE, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Bookings in these statuses occupy the master's time.
ACTIVE_STATUSES = ("pending", "confirmed")
BOOKING_STATUSES = ("pending", "confirmed", "completed", "cancelled", "no_show")


class Salon(Base):
    """One customer of the platform (a salon business). Everything else hangs off a salon."""

    __tablename__ = "salons"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)  # used in URLs: /premium, /premium/panel
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20), default="")
    plan: Mapped[str] = mapped_column(String(20), default="standard")
    kind: Mapped[str] = mapped_column(String(20), default="barber", server_default="barber")  # see app/kinds.py
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # a sales demo; its bot can be reused
    # What this business uses: "booking" (appointments), "orders" (catalog and orders), or both, comma separated.
    modules: Mapped[str] = mapped_column(String(40), default="booking", server_default="booking")
    delivery: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")  # orders: delivery offered
    pickup: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")  # orders: pickup offered
    delivery_fee: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # so'm, added to delivered orders
    min_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # so'm
    paid_until: Mapped[date] = mapped_column(Date)  # subscription is active through this day
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # platform can suspend
    description: Mapped[str] = mapped_column(Text, default="")
    instagram: Mapped[str] = mapped_column(String(60), default="")
    cover_url: Mapped[str | None] = mapped_column(String(80))
    logo_url: Mapped[str | None] = mapped_column(String(80))
    # The salon's own Telegram bot (token encrypted, see app/telegram.py).
    tg_token_sealed: Mapped[str | None] = mapped_column(Text)
    tg_bot_username: Mapped[str] = mapped_column(String(64), default="", server_default="")
    tg_webhook_secret: Mapped[str] = mapped_column(String(64), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Staff(Base):
    """A salon employee who logs into the panel with their own login and password."""

    __tablename__ = "staff"
    __table_args__ = (
        UniqueConstraint("salon_id", "login", name="staff_salon_login"),
        CheckConstraint("role IN ('owner', 'admin', 'barber')", name="staff_role_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    salon_id: Mapped[int] = mapped_column(ForeignKey("salons.id"), index=True)
    login: Mapped[str] = mapped_column(String(40))
    password_hash: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16))
    master_id: Mapped[int | None] = mapped_column(ForeignKey("masters.id"), unique=True)  # set for barbers
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # +1 signs out every device
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger)  # new-booking messages go here, once linked through the salon's bot
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(primary_key=True)
    salon_id: Mapped[int] = mapped_column(ForeignKey("salons.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(20), default="")
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    photo_url: Mapped[str | None] = mapped_column(String(500))
    landmark: Mapped[str] = mapped_column(String(255), default="")  # "mo'ljal": next to the metro, etc.
    hours_text: Mapped[str] = mapped_column(String(120), default="Har kuni 10:00–21:00")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Tashkent")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    masters: Mapped[list["Master"]] = relationship(back_populates="branch")


class User(Base):
    """A salon's client. The same phone in two salons is two separate clients."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("salon_id", "phone", name="users_salon_phone"),
        Index("users_salon_tg_chat", "salon_id", "tg_chat_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    salon_id: Mapped[int] = mapped_column(ForeignKey("salons.id"), index=True)
    phone: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120), default="")
    language: Mapped[str] = mapped_column(String(5), default="uz")
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger)  # set once the client logs in through the salon's bot
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Master(Base):
    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    photo_url: Mapped[str | None] = mapped_column(String(500))
    phone: Mapped[str] = mapped_column(String(20), default="")
    rank: Mapped[str] = mapped_column(String(40), default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    experience_years: Mapped[int | None] = mapped_column(SmallInteger)
    specialties: Mapped[str] = mapped_column(String(255), default="")  # comma separated: "Fade, Soqol, Klassika"
    instagram: Mapped[str] = mapped_column(String(60), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    branch: Mapped[Branch] = relationship(back_populates="masters")


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    salon_id: Mapped[int] = mapped_column(ForeignKey("salons.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(60), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class BranchService(Base):
    """Price and duration of a service in a given branch."""

    __tablename__ = "branch_services"
    __table_args__ = (CheckConstraint("duration_min > 0", name="branch_services_duration_check"),)

    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), primary_key=True)
    price: Mapped[int] = mapped_column(BigInteger)  # so'm
    duration_min: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class MasterService(Base):
    """Services a master performs, with optional per-master price/duration."""

    __tablename__ = "master_services"

    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), primary_key=True)
    price_override: Mapped[int | None] = mapped_column(BigInteger)
    duration_override: Mapped[int | None] = mapped_column(Integer)


class WorkingHours(Base):
    """Regular weekly schedule in the branch's local time. weekday: 0 = Monday."""

    __tablename__ = "working_hours"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="working_hours_weekday_check"),
        CheckConstraint("end_time > start_time", name="working_hours_range_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), index=True)
    weekday: Mapped[int] = mapped_column(SmallInteger)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)


class ScheduleException(Base):
    """day_off: whole day off. break: time blocked. extra: additional working time."""

    __tablename__ = "schedule_exceptions"
    __table_args__ = (
        CheckConstraint("kind IN ('day_off', 'break', 'extra')", name="schedule_exceptions_kind_check"),
        CheckConstraint(
            "kind = 'day_off' OR (start_time IS NOT NULL AND end_time IS NOT NULL AND end_time > start_time)",
            name="schedule_exceptions_range_check",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), index=True)
    date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(16))
    start_time: Mapped[time | None] = mapped_column(Time)
    end_time: Mapped[time | None] = mapped_column(Time)
    note: Mapped[str] = mapped_column(String(255), default="")


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="bookings_range_check"),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'completed', 'cancelled', 'no_show')",
            name="bookings_status_check",
        ),
        # The hard guarantee against double booking: Postgres rejects two active
        # bookings of one master whose time ranges overlap, whatever the app does.
        ExcludeConstraint(
            ("master_id", "="),
            ("period", "&&"),
            where=text("status IN ('pending', 'confirmed')"),
            using="gist",
            name="bookings_no_overlap",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="bookings_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    salon_id: Mapped[int] = mapped_column(ForeignKey("salons.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"))
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period = mapped_column(
        TSTZRANGE, Computed("tstzrange(starts_at, ends_at, '[)')", persisted=True)
    )
    total_price: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    source: Mapped[str] = mapped_column(String(16), default="web")  # web | admin
    comment: Mapped[str] = mapped_column(String(500), default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["BookingItem"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan", lazy="selectin"
    )
    master: Mapped[Master] = relationship(lazy="joined")
    branch: Mapped[Branch] = relationship(lazy="joined")
    user: Mapped[User] = relationship(lazy="joined")
    review: Mapped["Review | None"] = relationship(lazy="selectin", viewonly=True, uselist=False)


class BookingItem(Base):
    __tablename__ = "booking_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    service_name: Mapped[str] = mapped_column(String(120))
    price: Mapped[int] = mapped_column(BigInteger)
    duration_min: Mapped[int] = mapped_column(Integer)

    booking: Mapped[Booking] = relationship(back_populates="items")


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (UniqueConstraint("booking_id", "kind", name="reminders_booking_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))  # 24h | 2h
    send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    salon_id: Mapped[int] = mapped_column(ForeignKey("salons.id"))
    phone: Mapped[str] = mapped_column(String(20), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LoyaltyLedger(Base):
    """Points history; balance = SUM(points). Filled in version 2."""

    __tablename__ = "loyalty_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id"))
    points: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Photo(Base):
    """An uploaded image (salon cover/logo, branch photo, barber face, barber's work), served from /media/{id}.

    Kept in Postgres so it survives redeploys without extra storage; pages upload already-resized JPEG/WebP.
    """

    __tablename__ = "photos"
    __table_args__ = (
        CheckConstraint("kind IN ('cover', 'logo', 'branch', 'master', 'work', 'product')", name="photos_kind_check"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    salon_id: Mapped[int] = mapped_column(ForeignKey("salons.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    ref_id: Mapped[int | None] = mapped_column(Integer)  # branch or master id
    content_type: Mapped[str] = mapped_column(String(20))
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    size: Mapped[int] = mapped_column(Integer)
    caption: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Review(Base):
    """A client's stars and words about a visit. Only a completed booking can be reviewed, once."""

    __tablename__ = "reviews"
    __table_args__ = (CheckConstraint("rating BETWEEN 1 AND 5", name="reviews_rating_check"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    salon_id: Mapped[int] = mapped_column(ForeignKey("salons.id"), index=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), unique=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    rating: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str] = mapped_column(String(1000), default="")
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)  # the salon can hide abuse, not change stars
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TgLogin(Base):
    """One "log in with Telegram" attempt: the site waits while the client shares their number with the bot."""

    __tablename__ = "tg_logins"

    id: Mapped[int] = mapped_column(primary_key=True)
    salon_id: Mapped[int] = mapped_column(ForeignKey("salons.id"), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | done | used
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------- the platform's own team: owner, partner, sales people ----------

PLATFORM_ROLES = ("owner", "partner", "sales")
LEAD_STATUSES = ("new", "talked", "demo", "trial", "paid", "lost")


class PlatformUser(Base):
    """Someone who sells or runs the platform. The owner from PLATFORM_LOGIN needs no row."""

    __tablename__ = "platform_users"
    __table_args__ = (CheckConstraint("role IN ('owner', 'partner', 'sales')", name="platform_users_role_check"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(40), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Lead(Base):
    """A business we talk to, from the first call to a paying client (then it points at its salon)."""

    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint("status IN ('new', 'talked', 'demo', 'trial', 'paid', 'lost')", name="leads_status_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="other")
    phone: Mapped[str] = mapped_column(String(20), default="")
    contact: Mapped[str] = mapped_column(String(120), default="")  # who we talk to: owner, manager
    address: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(10), default="new")
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("platform_users.id"), index=True)  # None: the owner
    salon_id: Mapped[int | None] = mapped_column(ForeignKey("salons.id"), unique=True)
    next_step_on: Mapped[date | None] = mapped_column(Date)
    next_step: Mapped[str] = mapped_column(String(200), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("platform_users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Payment(Base):
    """Money a business paid us for its subscription."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    salon_id: Mapped[int] = mapped_column(ForeignKey("salons.id"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)  # so'm
    months: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(200), default="")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("platform_users.id"))
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------- orders: a catalog and orders taken through the bot ----------

ORDER_STATUSES = ("new", "accepted", "ready", "delivering", "done", "cancelled")
OPEN_ORDER_STATUSES = ("new", "accepted", "ready", "delivering")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    salon_id: Mapped[int] = mapped_column(ForeignKey("salons.id"), index=True)
    category: Mapped[str] = mapped_column(String(60), default="")
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="")
    price: Mapped[int] = mapped_column(BigInteger)  # so'm
    photo_url: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # off: hidden from the catalog ("tugadi")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("salon_id", "number", name="orders_salon_number"),
        UniqueConstraint("salon_id", "idempotency_key", name="orders_salon_idempotency"),
        CheckConstraint("status IN ('new', 'accepted', 'ready', 'delivering', 'done', 'cancelled')", name="orders_status_check"),
        CheckConstraint("fulfillment IN ('pickup', 'delivery')", name="orders_fulfillment_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    salon_id: Mapped[int] = mapped_column(ForeignKey("salons.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)  # #1, #2 ... per business: what staff and client say aloud
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(12), default="new")
    fulfillment: Mapped[str] = mapped_column(String(10), default="pickup")
    address: Mapped[str] = mapped_column(String(300), default="")
    comment: Mapped[str] = mapped_column(String(500), default="")
    items_total: Mapped[int] = mapped_column(BigInteger)
    delivery_fee: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan", lazy="selectin")
    user: Mapped[User] = relationship(lazy="joined")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    name: Mapped[str] = mapped_column(String(120))  # as it was when ordered
    price: Mapped[int] = mapped_column(BigInteger)
    qty: Mapped[int] = mapped_column(Integer)

    order: Mapped[Order] = relationship(back_populates="items")
