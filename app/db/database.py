from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.config import settings

def get_async_url(url: str) -> str:
    """Convert sync URL to async-compatible URL."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):  # Render sometimes uses this
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url  # sqlite+aiosqlite:// already correct

DATABASE_URL = get_async_url(settings.DATABASE_URL)

# Base engine kwargs for all environments
engine_kwargs = {
    "echo": settings.DEBUG,
    "pool_pre_ping": True,
}

# PostgreSQL (production / staging on Render)
if not DATABASE_URL.startswith("sqlite"):
    engine_kwargs.update({
        "pool_size": 5 if settings.DEBUG else 10,
        "max_overflow": 10 if settings.DEBUG else 20,
        "pool_recycle": 3600,
        "pool_timeout": 30,
        "connect_args": {
            "server_settings": {"application_name": "AMS-FastAPI"},
            "command_timeout": 60,
        },
    })
else:
    # SQLite for local dev (no pool options)
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_async_engine(DATABASE_URL, **engine_kwargs)

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

async def init_db():
    """Create all tables on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def close_db():
    """Dispose engine on shutdown."""
    await engine.dispose()