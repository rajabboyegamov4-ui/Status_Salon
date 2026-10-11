"""Orders: a client picks products in the bot, the business accepts, prepares and hands them over.

Status flow (what staff move it through; the client is told at every step):
    new -> accepted -> ready -> (delivering ->) done
    new | accepted -> cancelled           (the client can cancel only while it is still new)
"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, OrderItem, Product, Salon, User
from app.text import strip_controls

NEXT = {
    "new": ("accepted", "cancelled"),
    "accepted": ("ready", "cancelled"),
    "ready": ("delivering", "done"),
    "delivering": ("done",),
}
LABEL = {"new": "Yangi", "accepted": "Qabul qilindi", "ready": "Tayyor", "delivering": "Yo'lda", "done": "Berildi",
         "cancelled": "Bekor qilindi"}
ORDER_LOCK_NS = 7303  # advisory lock namespace: order numbers per business


class OrderError(Exception):
    pass


def has_orders(salon: Salon) -> bool:
    return "orders" in (salon.modules or "").split(",")


def has_booking(salon: Salon) -> bool:
    return "booking" in (salon.modules or "booking").split(",")


async def create_order(
    session: AsyncSession, salon: Salon, user: User, items: list[tuple[int, int]], fulfillment: str,
    address: str, comment: str, idempotency_key: str | None,
) -> tuple[Order, bool]:
    if not has_orders(salon):
        raise OrderError("Bu biznes buyurtma qabul qilmaydi")
    if idempotency_key:
        same = await session.scalar(select(Order).where(Order.salon_id == salon.id, Order.idempotency_key == idempotency_key))
        if same is not None:
            return same, False
    if fulfillment == "delivery" and not salon.delivery:
        raise OrderError("Yetkazib berish yo'q, olib ketish mumkin")
    if fulfillment == "pickup" and not salon.pickup:
        raise OrderError("Faqat yetkazib berish bor")
    address = strip_controls(address).strip()
    if fulfillment == "delivery" and len(address) < 5:
        raise OrderError("Yetkazish manzilini yozing")
    wanted: dict[int, int] = {}
    for product_id, qty in items:
        wanted[product_id] = wanted.get(product_id, 0) + qty
    if not wanted:
        raise OrderError("Savat bo'sh")
    products = {p.id: p for p in (await session.scalars(
        select(Product).where(Product.salon_id == salon.id, Product.id.in_(wanted), Product.is_active)
    )).all()}
    if len(products) != len(wanted):
        raise OrderError("Savatdagi ba'zi mahsulotlar endi yo'q. Sahifani yangilang.")
    lines = [OrderItem(product_id=pid, name=products[pid].name, price=products[pid].price, qty=min(qty, 99))
             for pid, qty in wanted.items()]
    items_total = sum(x.price * x.qty for x in lines)
    if items_total < (salon.min_order or 0):
        raise OrderError(f"Eng kam buyurtma {salon.min_order:,} so'm".replace(",", " "))
    fee = salon.delivery_fee if fulfillment == "delivery" else 0
    await session.execute(select(func.pg_advisory_xact_lock(ORDER_LOCK_NS, salon.id)))
    number = (await session.scalar(select(func.max(Order.number)).where(Order.salon_id == salon.id)) or 0) + 1
    order = Order(salon_id=salon.id, number=number, user_id=user.id, fulfillment=fulfillment,
                  address=address[:300] if fulfillment == "delivery" else "", comment=strip_controls(comment).strip()[:500],
                  items_total=items_total, delivery_fee=fee, total=items_total + fee, idempotency_key=idempotency_key, items=lines)
    session.add(order)
    try:
        await session.commit()
    except IntegrityError:  # the same key raced in from a second tap
        await session.rollback()
        same = await session.scalar(select(Order).where(Order.salon_id == salon.id, Order.idempotency_key == idempotency_key))
        if same is None:
            raise
        return same, False
    return await load_order(session, order.id), True


async def load_order(session: AsyncSession, order_id: int) -> Order | None:
    """Fresh from the database (status, items), without expiring anything else the caller holds."""
    return await session.scalar(select(Order).where(Order.id == order_id).execution_options(populate_existing=True))


async def lock_order(session: AsyncSession, order_id: int) -> Order | None:
    """Two taps on two phones: the second waits for the first and sees its result."""
    await session.execute(select(Order.id).where(Order.id == order_id).with_for_update())
    return await load_order(session, order_id)


async def move(session: AsyncSession, order: Order, status: str, by_client: bool = False) -> Order:
    if by_client and not (order.status == "new" and status == "cancelled"):
        raise OrderError("Buyurtma qabul qilingan. Bekor qilish uchun bizga qo'ng'iroq qiling.")
    if status not in NEXT.get(order.status, ()):
        raise OrderError(f"Buyurtma holati: {LABEL[order.status]}. Bu amal endi mumkin emas.")
    if status == "delivering" and order.fulfillment != "delivery":
        raise OrderError("Bu buyurtma olib ketiladi")
    order.status = status
    await session.commit()
    return await load_order(session, order.id)
