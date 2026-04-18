from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base


ADMIN_ROLE_SQL_DEFAULT = "user"


def create_engine() -> AsyncEngine:
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_async_engine(settings.database_url, future=True, echo=False, connect_args=connect_args)


engine = create_engine()
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        if conn.dialect.name == "sqlite":
            user_columns = {row[1] for row in (await conn.exec_driver_sql("PRAGMA table_info(users)")).all()}
            if "is_blocked" not in user_columns:
                await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN is_blocked BOOLEAN NOT NULL DEFAULT 0")
            if "admin_role" not in user_columns:
                await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN admin_role VARCHAR(30) NOT NULL DEFAULT 'user'")
                await conn.exec_driver_sql("UPDATE users SET admin_role = 'admin' WHERE is_admin = 1 AND admin_role = 'user'")

            subscription_columns = {row[1] for row in (await conn.exec_driver_sql("PRAGMA table_info(subscriptions)")).all()}
            if "expiry_notice_sent_at" not in subscription_columns:
                await conn.exec_driver_sql("ALTER TABLE subscriptions ADD COLUMN expiry_notice_sent_at DATETIME")
        elif conn.dialect.name == "postgresql":
            await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_blocked BOOLEAN NOT NULL DEFAULT FALSE")
            await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN IF NOT EXISTS admin_role VARCHAR(30) NOT NULL DEFAULT 'user'")
            await conn.exec_driver_sql("UPDATE users SET admin_role = 'admin' WHERE is_admin = TRUE AND admin_role = 'user'")
            await conn.exec_driver_sql("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS expiry_notice_sent_at TIMESTAMPTZ")
