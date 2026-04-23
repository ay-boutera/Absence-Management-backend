"""sprint3 — Session model, AbsenceCorrection, updated Absence

Revision ID: f1a2b3c4d5e6
Revises: e9c1a7b2d4f6
Create Date: 2026-04-23 00:00:00.000000

Changes:
  - New enums: sessionstatusenum, absencesourceenum, correctionstatusenum
  - New table: sessions
  - New table: session_attendance_summaries
  - New table: absence_corrections
  - Rebuild absences table: add session_id FK, recorded_by, is_absent,
    source enum, synced_at, created_at, updated_at; add UNIQUE constraint
    (session_id, student_matricule); drop planning_session_id
  - Add nom column to salles
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.database import Base
from app import models as _models  # noqa: F401 — registers all ORM models

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "845507976fa6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # ── Drop all existing tables and types (force-rebuild pattern) ────────────
    reflected_metadata = sa.MetaData()
    reflected_metadata.reflect(bind=bind)

    for table in reversed(reflected_metadata.sorted_tables):
        if table.name != "alembic_version":
            table.drop(bind=bind, checkfirst=True)

    if bind.dialect.name == "postgresql":
        enum_names = bind.execute(
            sa.text(
                """
                SELECT t.typname
                FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE t.typtype = 'e' AND n.nspname = current_schema()
                """
            )
        ).scalars().all()

        for enum_name in enum_names:
            bind.execute(sa.text(f'DROP TYPE IF EXISTS "{enum_name}" CASCADE'))

    # ── Recreate from current ORM models ─────────────────────────────────────
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()

    reflected_metadata = sa.MetaData()
    reflected_metadata.reflect(bind=bind)

    for table in reversed(reflected_metadata.sorted_tables):
        if table.name != "alembic_version":
            table.drop(bind=bind, checkfirst=True)
