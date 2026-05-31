import asyncio
from sqlalchemy import select
from app.db import engine
from app.models.student import AcademicStudent, Student

async def main():
    async with engine.connect() as conn:
        res = await conn.execute(select(AcademicStudent))
        rows = res.fetchall()
        print(f"AcademicStudent rows: {len(rows)}")
        for r in rows:
            print(dict(r._mapping))
            
        res2 = await conn.execute(select(Student))
        rows2 = res2.fetchall()
        print(f"Student rows: {len(rows2)}")
        for r in rows2:
            print(dict(r._mapping))

asyncio.run(main())
