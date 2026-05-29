import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.asyncio
async def test_my_schedule(auth_client_student):
    res = await auth_client_student.get("/api/v1/planning/my-schedule")
    print("\nSTATUS:", res.status_code)
    print("RESPONSE:", res.text)
    assert res.status_code == 200

