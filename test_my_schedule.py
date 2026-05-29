import asyncio
from httpx import AsyncClient
from app.db import AsyncSessionLocal
from app.models.student import Student
from app.helpers.security import create_access_token
from sqlalchemy import select

async def run():
    try:
        async with AsyncSessionLocal() as session:
            student = (await session.execute(select(Student))).scalars().first()
            if not student:
                print("No student found, creating...")
                student = Student(
                    first_name="Test",
                    last_name="Test",
                    email="teststudent2@esi-sba.dz",
                    program="INFO",
                    level="L3",
                    student_id="ST1234",
                    group="G1"
                )
                session.add(student)
                await session.commit()
                await session.refresh(student)
        
            token = create_access_token({"sub": str(student.id), "role": "student"})
        print(f"Token created: {token[:10]}...")
            
        async with AsyncClient(base_url="http://127.0.0.1:8012") as client:
            res = await client.get("/api/v1/planning/my-schedule", headers={"Authorization": f"Bearer {token}"})
            print(res.status_code)
            print(res.text)
    except Exception as e:
        print("ERROR:", e)

asyncio.run(run())
