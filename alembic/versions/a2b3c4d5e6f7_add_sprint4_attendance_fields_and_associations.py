"""Sprint 4 — attendance participation, student status, session association tables

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-04-23 12:00:00.000000

Changes:
  - Add `participation` column (VARCHAR 10, nullable) to `absences`
  - Add `status` column (VARCHAR 20, NOT NULL, default 'normal') to `students`
  - Create `session_groups` table (session_id, group_name)
  - Create `session_students` table (session_id, student_matricule)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = inspector.get_table_names()

    # ── absences: add participation ───────────────────────────────────────────
    absence_cols = [c["name"] for c in inspector.get_columns("absences")]
    if "participation" not in absence_cols:
        op.add_column(
            "absences",
            sa.Column("participation", sa.String(10), nullable=True),
        )

    # ── students: add status ──────────────────────────────────────────────────
    student_cols = [c["name"] for c in inspector.get_columns("students")]
    if "status" not in student_cols:
        op.add_column(
            "students",
            sa.Column(
                "status",
                sa.String(20),
                server_default="normal",
                nullable=False,
            ),
        )

    # ── session_groups ────────────────────────────────────────────────────────
    if "session_groups" not in existing_tables:
        op.create_table(
            "session_groups",
            sa.Column(
                "session_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column("group_name", sa.String(50), nullable=False),
            sa.ForeignKeyConstraint(
                ["session_id"],
                ["sessions.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("session_id", "group_name"),
        )

    # ── session_students ──────────────────────────────────────────────────────
    if "session_students" not in existing_tables:
        op.create_table(
            "session_students",
            sa.Column(
                "session_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column("student_matricule", sa.String(50), nullable=False),
            sa.ForeignKeyConstraint(
                ["session_id"],
                ["sessions.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["student_matricule"],
                ["students.matricule"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("session_id", "student_matricule"),
        )


def downgrade() -> None:
    op.drop_table("session_students")
    op.drop_table("session_groups")
    op.drop_column("students", "status")
    op.drop_column("absences", "participation")
