import asyncio
import sys
from app.db import engine
from app.db.database import Base
import app.models

async def init_db():
    print("Creating all tables from models...")
    try:
        async with engine.begin() as conn:
            # This drops and creates all tables defined in your models
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        print("✅ Tables created successfully.")
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(init_db())
