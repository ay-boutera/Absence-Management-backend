"""
One-time backfill: sync all student_users rows into the students (AcademicStudent) table.
Useful for students added manually before this fix was applied.
"""
import asyncio
from sqlalchemy import select
from app.db import engine
from app.models.student import Student, AcademicStudent
from sqlalchemy.ext.asyncio import AsyncSession

async def main():
    async with AsyncSession(engine) as session:
        async with session.begin():
            students = (await session.execute(select(Student))).scalars().all()
            created = 0
            updated = 0
            for s in students:
                matricule = str(s.student_id or "").strip()
                if not matricule:
                    continue
                existing = (
                    await session.execute(
                        select(AcademicStudent).where(AcademicStudent.matricule == matricule)
                    )
                ).scalar_one_or_none()
                if existing is None:
                    session.add(AcademicStudent(
                        matricule=matricule,
                        nom=str(s.last_name or "").strip(),
                        prenom=str(s.first_name or "").strip(),
                        filiere=str(s.program or "").strip(),
                        niveau=str(s.level or "").strip(),
                        groupe=str(s.group or "").strip(),
                        email=str(s.email or "").strip(),
                    ))
                    created += 1
                    print(f"  [CREATE] {matricule} - {s.last_name} {s.first_name}")
                else:
                    # Only update if the AcademicStudent has no richer data
                    existing.nom = str(s.last_name or "").strip()
                    existing.prenom = str(s.first_name or "").strip()
                    existing.filiere = str(s.program or "").strip()
                    existing.niveau = str(s.level or "").strip()
                    existing.groupe = str(s.group or "").strip()
                    existing.email = str(s.email or "").strip()
                    session.add(existing)
                    updated += 1
                    print(f"  [UPDATE] {matricule} - {s.last_name} {s.first_name}")

    print(f"\n✅ Done: {created} created, {updated} updated in 'students' table.")

asyncio.run(main())
