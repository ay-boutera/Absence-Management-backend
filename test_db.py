import asyncio
from httpx import AsyncClient
from app.db import AsyncSessionLocal, engine
from app.models.student import Student
from app.helpers.security import create_access_token
from sqlalchemy import select

async def run():
    async with AsyncSessionLocal() as session:
        # make sure we have a student with level 1CS
        student = (await session.execute(select(Student))).scalars().first()
        if not student:
            print("No student found. Run the app setup.")
            return

        print("Original Student level:", student.level)
        student.level = "1CS" # valid enum
        session.add(student)
        await session.commit()
        
        token = create_access_token({"sub": str(student.id), "role": "student"})
        
    async with AsyncClient(base_url="http://127.0.0.1:8012") as client:
        res = await client.get("/api/v1/planning/my-schedule", headers={"Authorization": f"Bearer {token}"})
        print("Status with '1CS':", res.status_code)

    async with AsyncSessionLocal() as session:
        student.level = "L3" # invalid enum
        session.add(student)
        await session.commit()

    async with AsyncClient(base_url="http://127.0.0.1:8012") as client:
        res2 = await client.get("/api/v1/planning/my-schedule", headers={"Authorization": f"Bearer {token}"})
        print("Status with 'L3':", res2.status_code)
        
    async with AsyncSessionLocal() as session:
        student.level = "1CS"
        session.add(student)
        await session.commit()

    await engine.dispose()

asyncio.run(run())
