import asyncio
import sys
from app.db import engine
from app.routers.accounts.teachers import get_teacher_profile
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
import json

async def test():
    async with AsyncSession(engine) as db:
        try:
            profile = await get_teacher_profile(matricule="ENS002", db=db, current_user=None)
            print(profile.model_dump_json(indent=2))
        except HTTPException as e:
            print(f"HTTP error: {e.status_code} - {e.detail}")

asyncio.run(test())
