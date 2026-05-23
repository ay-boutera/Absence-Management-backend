from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.config import settings
import ssl


def get_async_url(url: str) -> str:
    # Strip query-string params that asyncpg doesn't understand
    # (e.g. ?sslmode=require&channel_binding=require from Neon URL)
    base = url.split("?")[0]
    if base.startswith("postgresql://"):
        return base.replace("postgresql://", "postgresql+asyncpg://", 1)
    if base.startswith("postgres://"):  # Render legacy format
        return base.replace("postgres://", "postgresql+asyncpg://", 1)
    return base


def _is_neon_url(url: str) -> bool:
    return "neon.tech" in url


DATABASE_URL = get_async_url(settings.DATABASE_URL)

# Neon requires SSL; asyncpg needs it passed via connect_args (not via URL query params)
_connect_args: dict = {
    "server_settings": {
        "application_name": "AMS-FastAPI",
        "search_path": "public",
    },
    "command_timeout": 60,
}
if _is_neon_url(settings.DATABASE_URL):
    _connect_args["ssl"] = ssl.create_default_context()

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    pool_timeout=30,
    connect_args=_connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base()


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_db():
    await engine.dispose()
