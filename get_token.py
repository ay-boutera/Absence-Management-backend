import asyncio
from app.db import AsyncSessionLocal, engine
from app.models.student import Student
from app.helpers.security import create_access_token
from sqlalchemy import select

async def run():
    async with AsyncSessionLocal() as session:
        student = (await session.execute(select(Student))).scalars().first()
        if not student:
            print("No student found in real db!")
            token = "NO_STUDENT"
        else:
            token = create_access_token({"sub": str(student.id), "role": "student"})
            print(token)
    await engine.dispose()

asyncio.run(run())
