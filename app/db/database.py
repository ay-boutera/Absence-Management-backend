from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    # pool_size et max_overflow ne fonctionnent qu'avec PostgreSQL
    **(
        {}
        if settings.DATABASE_URL.startswith("sqlite")
        else {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_pre_ping": True,
        }
    )
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
