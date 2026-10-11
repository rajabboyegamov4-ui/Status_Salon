"""Orders module: the catalog and orders for a client, the orders desk and catalog editing for staff.

/api/s/{slug}/catalog, /orders, /me/orders            client (session from the bot's Mini App or Telegram login)
/api/s/{slug}/staff/orders, /staff/products, /staff/shop  owner and administrators
"""

import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth
from app.db import get_session
from app.models import OPEN_ORDER_STATUSES, Order, Photo, Product, Salon, Staff, User
from app.notifications import notify_staff_order, send_order_update
from app.services import orders as svc
from app.services import profiles
from app.tenancy import active_salon, get_salon, subscription_active

router = APIRouter(tags=["orders"])


# ---------- shapes ----------

class ProductOut(BaseModel):
    id: int
    category: str
    name: str
    description: str
    price: int
    photo_url: str | None
    is_active: bool
    sort: int


class ShopInfo(BaseModel):
    delivery: bool
    pickup: bool
    delivery_fee: int
    min_order: int


class Catalog(BaseModel):
    shop: ShopInfo
    products: list[ProductOut]


class OrderLine(BaseModel):
    product_id: int
    qty: int = Field(ge=1, le=99)


class OrderIn(BaseModel):
    items: list[OrderLine] = Field(min_length=1, max_length=50)
    fulfillment: str = Field(pattern="^(pickup|delivery)$")
    address: str = Field("", max_length=300)
    comment: str = Field("", max_length=500)


class OrderItemOut(BaseModel):
    product_id: int
    name: str
    price: int
    qty: int


class OrderOut(BaseModel):
    id: int
    number: int
    status: str
    status_label: str
    fulfillment: str
    address: str
    comment: str
    items: list[OrderItemOut]
    items_total: int
    delivery_fee: int
    total: int
    created_at: str
    client_name: str = ""
    client_phone: str = ""

    @classmethod
    def of(cls, o: Order, staff: bool = False) -> "OrderOut":
        return cls(
            id=o.id, number=o.number, status=o.status, status_label=svc.LABEL[o.status], fulfillment=o.fulfillment,
            address=o.address, comment=o.comment, items_total=o.items_total, delivery_fee=o.delivery_fee, total=o.total,
            items=[OrderItemOut(product_id=i.product_id, name=i.name, price=i.price, qty=i.qty) for i in o.items],
            created_at=o.created_at.isoformat(),
            client_name=(o.user.name or "Mijoz") if staff else "", client_phone=o.user.phone if staff else "",
        )


class StatusIn(BaseModel):
    status: str = Field(pattern="^(accepted|ready|delivering|done|cancelled)$")


class ProductIn(BaseModel):
    category: str = Field("", max_length=60)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=500)
    price: int = Field(ge=0, le=1_000_000_000)
    is_active: bool = True
    sort: int = 0


class ProductPatch(BaseModel):
    category: str | None = Field(None, max_length=60)
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=500)
    price: int | None = Field(None, ge=0, le=1_000_000_000)
    is_active: bool | None = None
    sort: int | None = None


class ShopPatch(BaseModel):
    delivery: bool | None = None
    pickup: bool | None = None
    delivery_fee: int | None = Field(None, ge=0, le=10_000_000)
    min_order: int | None = Field(None, ge=0, le=100_000_000)


def _product(p: Product) -> ProductOut:
    return ProductOut(id=p.id, category=p.category, name=p.name, description=p.description, price=p.price,
                      photo_url=p.photo_url, is_active=p.is_active, sort=p.sort)


def _shop(salon: Salon) -> ShopInfo:
    return ShopInfo(delivery=salon.delivery, pickup=salon.pickup, delivery_fee=salon.delivery_fee, min_order=salon.min_order)


def _need_orders(salon: Salon) -> None:
    if not svc.has_orders(salon):
        raise HTTPException(404, "Bu biznes buyurtma qabul qilmaydi")


async def orders_manager(staff: Staff = Depends(auth.manager), salon: Salon = Depends(get_salon)) -> Staff:
    _need_orders(salon)
    return staff


async def orders_writer(staff: Staff = Depends(orders_manager), salon: Salon = Depends(get_salon)) -> Staff:
    if not subscription_active(salon):
        raise HTTPException(402, "Obuna muddati tugagan. Panel faqat ko'rish rejimida ishlaydi.")
    return staff


# ---------- client ----------

@router.get("/s/{slug}/catalog", response_model=Catalog)
async def catalog(salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)):
    _need_orders(salon)
    rows = (await session.scalars(select(Product).where(Product.salon_id == salon.id, Product.is_active)
                                  .order_by(Product.sort, Product.category, Product.id))).all()
    return Catalog(shop=_shop(salon), products=[_product(p) for p in rows])


@router.post("/s/{slug}/orders", response_model=OrderOut, status_code=201)
async def place_order(
    body: OrderIn, background: BackgroundTasks, response: Response,
    idempotency_key: str | None = Header(None, max_length=64),
    salon: Salon = Depends(active_salon), user: User = Depends(auth.current_client), session: AsyncSession = Depends(get_session),
):
    try:
        order, created = await svc.create_order(session, salon, user, [(x.product_id, x.qty) for x in body.items],
                                                body.fulfillment, body.address, body.comment, idempotency_key)
    except svc.OrderError as e:
        raise HTTPException(422, str(e))
    if created:
        background.add_task(send_order_update, order.id)
        background.add_task(notify_staff_order, order.id, "new")
    else:
        response.status_code = 200
    return OrderOut.of(order)


@router.get("/s/{slug}/me/orders", response_model=list[OrderOut])
async def my_orders(salon: Salon = Depends(get_salon), user: User = Depends(auth.current_client), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Order).where(Order.salon_id == salon.id, Order.user_id == user.id)
                                  .order_by(Order.id.desc()).limit(30))).unique().all()
    return [OrderOut.of(o) for o in rows]


@router.post("/s/{slug}/orders/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(order_id: int, background: BackgroundTasks, salon: Salon = Depends(get_salon),
                       user: User = Depends(auth.current_client), session: AsyncSession = Depends(get_session)):
    order = await session.get(Order, order_id, with_for_update=True)
    if order is None or order.salon_id != salon.id or order.user_id != user.id:
        raise HTTPException(404, "Buyurtma topilmadi")
    try:
        order = await svc.move(session, order, "cancelled", by_client=True)
    except svc.OrderError as e:
        raise HTTPException(422, str(e))
    background.add_task(notify_staff_order, order.id, "cancelled")
    return OrderOut.of(order)


# ---------- staff: the orders desk ----------

@router.get("/s/{slug}/staff/orders", response_model=list[OrderOut])
async def staff_orders(scope: str = Query("open", pattern="^(open|closed)$"), staff: Staff = Depends(orders_manager),
                       salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)):
    stmt = select(Order).where(Order.salon_id == salon.id)
    stmt = stmt.where(Order.status.in_(OPEN_ORDER_STATUSES)).order_by(Order.number) if scope == "open" \
        else stmt.where(Order.status.not_in(OPEN_ORDER_STATUSES)).order_by(Order.id.desc()).limit(100)
    return [OrderOut.of(o, staff=True) for o in (await session.scalars(stmt)).unique().all()]


@router.patch("/s/{slug}/staff/orders/{order_id}", response_model=OrderOut)
async def staff_move(order_id: int, body: StatusIn, background: BackgroundTasks, staff: Staff = Depends(orders_writer),
                     salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)):
    actor = staff.id
    order = await session.get(Order, order_id, with_for_update=True)  # two taps on two phones: one wins
    if order is None or order.salon_id != salon.id:
        raise HTTPException(404, "Buyurtma topilmadi")
    try:
        order = await svc.move(session, order, body.status)
    except svc.OrderError as e:
        raise HTTPException(409, str(e))
    background.add_task(send_order_update, order.id)
    background.add_task(notify_staff_order, order.id, order.status, actor)
    return OrderOut.of(order, staff=True)


# ---------- staff: catalog and shop settings ----------

@router.get("/s/{slug}/staff/products", response_model=list[ProductOut])
async def products(staff: Staff = Depends(orders_manager), salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Product).where(Product.salon_id == salon.id)
                                  .order_by(Product.sort, Product.category, Product.id))).all()
    return [_product(p) for p in rows]


@router.post("/s/{slug}/staff/products", response_model=ProductOut, status_code=201)
async def product_add(body: ProductIn, staff: Staff = Depends(orders_writer), salon: Salon = Depends(get_salon),
                      session: AsyncSession = Depends(get_session)):
    p = Product(salon_id=salon.id, **{k: (v.strip() if isinstance(v, str) else v) for k, v in body.model_dump().items()})
    session.add(p)
    await session.commit()
    return _product(p)


async def _own_product(session: AsyncSession, salon: Salon, product_id: int) -> Product:
    p = await session.get(Product, product_id)
    if p is None or p.salon_id != salon.id:
        raise HTTPException(404, "Mahsulot topilmadi")
    return p


@router.patch("/s/{slug}/staff/products/{product_id}", response_model=ProductOut)
async def product_edit(product_id: int, body: ProductPatch, staff: Staff = Depends(orders_writer),
                       salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)):
    p = await _own_product(session, salon, product_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(p, k, v.strip() if isinstance(v, str) else v)
    await session.commit()
    return _product(p)


@router.post("/s/{slug}/staff/products/{product_id}/photo", response_model=ProductOut)
async def product_photo(product_id: int, request: Request, staff: Staff = Depends(orders_writer),
                        salon: Salon = Depends(get_salon), session: AsyncSession = Depends(get_session)):
    """The image is the request body (JPEG, PNG or WebP, already resized by the page). Replaces the old one."""
    p = await _own_product(session, salon, product_id)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > profiles.MAX_PHOTO_BYTES:
            raise HTTPException(413, "Rasm juda katta (3 MB dan oshmasin)")
    try:
        content_type = profiles.sniff(bytes(body))
    except profiles.PhotoError as e:
        raise HTTPException(422, str(e))
    old = (await session.scalars(select(Photo).where(Photo.salon_id == salon.id, Photo.kind == "product", Photo.ref_id == p.id))).all()
    for x in old:
        await session.delete(x)
    photo = Photo(id=secrets.token_hex(16), salon_id=salon.id, kind="product", ref_id=p.id, content_type=content_type,
                  data=bytes(body), size=len(body))
    session.add(photo)
    p.photo_url = profiles.url_of(photo.id)
    await session.commit()
    return _product(p)


@router.patch("/s/{slug}/staff/shop", response_model=ShopInfo)
async def shop_settings(body: ShopPatch, staff: Staff = Depends(orders_writer), salon: Salon = Depends(get_salon),
                        session: AsyncSession = Depends(get_session)):
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(salon, k, v)
    if not salon.delivery and not salon.pickup:
        raise HTTPException(422, "Yetkazish yoki olib ketishdan kamida bittasi yoqilgan bo'lsin")
    await session.commit()
    return _shop(salon)
