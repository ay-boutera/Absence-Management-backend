import asyncio
from sqlalchemy import select
from app.db import engine
from app.models.student import AcademicStudent

async def main():
    async with engine.connect() as conn:
        result = await conn.execute(
            select(AcademicStudent.matricule, AcademicStudent.groupe, AcademicStudent.niveau)
            .where(AcademicStudent.matricule == '2202220394')
        )
        for row in result:
            print(dict(row._mapping))

asyncio.run(main())
