"""
Debug script: run with
  source .venv/bin/activate && python debug_mymodules.py
"""
import asyncio
from sqlalchemy import select, func, and_, text
from app.db import AsyncSessionLocal
from app.models.planning_session import PlanningSession
from app.models.student import AcademicStudent


async def main():
    async with AsyncSessionLocal() as db:

        # ── 1. Show the student record we care about ──────────────────────────
        print("\n=== AcademicStudent rows (first 10) ===")
        students = (await db.execute(select(AcademicStudent).limit(10))).scalars().all()
        for s in students:
            print(f"  matricule={s.matricule!r}  groupe={s.groupe!r}  niveau={s.niveau!r}")

        # ── 2. Show all PlanningSession rows ──────────────────────────────────
        print("\n=== PlanningSession rows (first 20) ===")
        pss = (await db.execute(select(PlanningSession).limit(20))).scalars().all()
        for ps in pss:
            print(f"  year={ps.year!r}  group={ps.group!r}  subject={ps.subject!r}  day={ps.day!r}")

        # ── 3. Simulate the exact query from /my-modules for a known student ──
        # Change these to match the student you're testing with:
        test_groupe = "G4"
        test_niveau = "1CS"

        print(f"\n=== Simulating /my-modules for groupe={test_groupe!r} niveau={test_niveau!r} ===")

        # Raw values comparison
        raw = (await db.execute(
            select(PlanningSession.year, PlanningSession.group, PlanningSession.subject)
            .where(PlanningSession.group == test_groupe)
        )).all()
        print(f"\n  Direct equality (group == {test_groupe!r}): {len(raw)} rows")
        for r in raw:
            print(f"    year={r[0]!r}  group={r[1]!r}  subject={r[2]!r}")

        # With func.lower/trim as used in the endpoint
        lower_query = (await db.execute(
            select(PlanningSession.subject).distinct()
            .where(
                and_(
                    func.lower(func.trim(PlanningSession.year)) == test_niveau.lower(),
                    func.lower(func.trim(PlanningSession.group)) == test_groupe.lower(),
                )
            )
        )).scalars().all()
        print(f"\n  With func.lower/trim: {lower_query}")

        # Check what year values actually look like in DB
        print("\n=== Raw year/group values stored in planning_sessions ===")
        raw_vals = (await db.execute(
            text("SELECT year, \"group\", subject FROM planning_sessions LIMIT 20")
        )).all()
        for r in raw_vals:
            print(f"  year={r[0]!r}  group={r[1]!r}  subject={r[2]!r}")


asyncio.run(main())
