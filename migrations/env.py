import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401  (registers tables)
from app.config import settings
from app.db import Base, normalize_db_url

target_metadata = Base.metadata


def do_run(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_online():
    engine = create_async_engine(normalize_db_url(settings.database_url))
    async with engine.connect() as conn:
        await conn.run_sync(do_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=normalize_db_url(settings.database_url).render_as_string(hide_password=False),
                      target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_online())
