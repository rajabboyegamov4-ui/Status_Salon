from collections.abc import AsyncIterator

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings


def normalize_db_url(raw: str) -> URL:
    """Neon gives `postgresql://...?sslmode=require`; asyncpg wants `ssl=require`."""
    url = make_url(raw)
    if url.drivername in ("postgresql", "postgres"):
        url = url.set(drivername="postgresql+asyncpg")
    query = dict(url.query)
    if "sslmode" in query:
        query["ssl"] = query.pop("sslmode")
    query.pop("channel_binding", None)
    return url.set(query=query)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    normalize_db_url(settings.database_url),
    pool_pre_ping=True,
    **({"poolclass": NullPool} if settings.db_null_pool else {}),
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
