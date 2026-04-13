"""Recreate planning_sessions as weekly timetable model

Revision ID: c1d2e3f4a5b6
Revises: b7f1c2a9d4e6
Create Date: 2026-04-13 22:00:00.000000

Changes
-------
- Drop old `absences` table (FK dependency on planning_sessions)
- Drop old `planning_sessions` table (id_seance/code_module/date schema)
- Recreate `planning_sessions` with the new weekly-timetable schema:
    year, section, speciality, semester, day, time_start, time_end,
    type, subject, teacher_id (→ teacher_users.id), room, group
    + UniqueConstraint uq_planning_session
- Recreate `absences` table (FK → new planning_sessions.id)
- Ensure importtype enum has 'PLANNING' value (it already does from sprint2)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b7f1c2a9d4e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _table_exists(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _index_exists(bind, table: str, index: str) -> bool:
    inspector = sa.inspect(bind)
    indexes = [i["name"] for i in inspector.get_indexes(table)] if inspector.has_table(table) else []
    return index in indexes


# ── Upgrade ───────────────────────────────────────────────────────────────────
def upgrade() -> None:
    bind = op.get_bind()

    # 1. Drop dependant tables in correct FK order
    if _table_exists(bind, "absences"):
        op.drop_index("ix_absences_student_matricule", table_name="absences")
        op.drop_index("ix_absences_planning_session_id", table_name="absences")
        op.drop_table("absences")

    # 2. Drop old planning_sessions (all its indexes first)
    if _table_exists(bind, "planning_sessions"):
        for idx in [
            "ix_planning_sessions_id_seance",
            "ix_planning_sessions_id_enseignant",
            "uq_planning_session",       # might not exist yet on old schema
        ]:
            try:
                if _index_exists(bind, "planning_sessions", idx):
                    op.drop_index(idx, table_name="planning_sessions")
            except Exception:
                pass
        op.drop_table("planning_sessions")

    # 3. Create new planning_sessions table
    op.create_table(
        "planning_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # Academic context
        sa.Column("year",       sa.String(length=10),  nullable=False),
        sa.Column("section",    sa.String(length=5),   nullable=True),
        sa.Column("speciality", sa.String(length=10),  nullable=True),
        sa.Column("semester",   sa.String(length=2),   nullable=False),
        # Schedule
        sa.Column("day",        sa.String(length=20),  nullable=False),
        sa.Column("time_start", sa.Time(),             nullable=False),
        sa.Column("time_end",   sa.Time(),             nullable=False),
        # Content
        sa.Column("type",       sa.String(length=20),  nullable=False),
        sa.Column("subject",    sa.String(length=255), nullable=False),
        sa.Column("room",       sa.String(length=100), nullable=True),
        sa.Column("group",      sa.String(length=20),  nullable=True),
        # Teacher FK → teacher_users.id (nullable: TBD sessions allowed)
        sa.Column("teacher_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["teacher_id"], ["teacher_users.id"],
            ondelete="SET NULL",
            name="fk_planning_session_teacher",
        ),
        sa.UniqueConstraint(
            "year", "section", "speciality", "semester", "day",
            "time_start", "type", "subject", "group", "teacher_id",
            name="uq_planning_session",
        ),
    )

    # Index on teacher_id for JOIN performance
    op.create_index(
        "ix_planning_sessions_teacher_id",
        "planning_sessions",
        ["teacher_id"],
        unique=False,
    )

    # 4. Recreate absences table (FK → new planning_sessions.id)
    op.create_table(
        "absences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_matricule", sa.String(length=50), nullable=False),
        sa.Column("planning_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("statut_justificatif", sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["student_matricule"], ["students.matricule"],
            ondelete="RESTRICT",
            name="fk_absence_student",
        ),
        sa.ForeignKeyConstraint(
            ["planning_session_id"], ["planning_sessions.id"],
            ondelete="CASCADE",
            name="fk_absence_session",
        ),
    )
    op.create_index(
        "ix_absences_student_matricule",
        "absences",
        ["student_matricule"],
        unique=False,
    )
    op.create_index(
        "ix_absences_planning_session_id",
        "absences",
        ["planning_session_id"],
        unique=False,
    )

    # 5. Ensure importtype enum includes 'PLANNING' (was added in sprint2 migration)
    op.execute("ALTER TYPE importtype ADD VALUE IF NOT EXISTS 'PLANNING'")


# ── Downgrade ─────────────────────────────────────────────────────────────────
def downgrade() -> None:
    bind = op.get_bind()

    # 1. Drop new absences
    if _table_exists(bind, "absences"):
        op.drop_index("ix_absences_planning_session_id", table_name="absences")
        op.drop_index("ix_absences_student_matricule", table_name="absences")
        op.drop_table("absences")

    # 2. Drop new planning_sessions
    if _table_exists(bind, "planning_sessions"):
        if _index_exists(bind, "planning_sessions", "ix_planning_sessions_teacher_id"):
            op.drop_index("ix_planning_sessions_teacher_id", table_name="planning_sessions")
        op.drop_table("planning_sessions")

    # 3. Recreate OLD planning_sessions schema
    session_type_enum = postgresql.ENUM(
        "COURS", "TD", "TP", "EXAMEN",
        name="sessiontype",
        create_type=False,
    )
    session_type_enum.create(bind, checkfirst=True)

    op.create_table(
        "planning_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id_seance", sa.String(length=100), nullable=False),
        sa.Column("code_module", sa.String(length=50), nullable=False),
        sa.Column("type_seance", session_type_enum, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("heure_debut", sa.Time(), nullable=False),
        sa.Column("heure_fin", sa.Time(), nullable=False),
        sa.Column("salle", sa.String(length=50), nullable=False),
        sa.Column("id_enseignant", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["code_module"], ["modules.code"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["id_enseignant"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["salle"], ["salles.code"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_planning_sessions_id_seance",
        "planning_sessions", ["id_seance"], unique=True,
    )
    op.create_index(
        "ix_planning_sessions_id_enseignant",
        "planning_sessions", ["id_enseignant"], unique=False,
    )

    # 4. Recreate old absences table
    op.create_table(
        "absences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_matricule", sa.String(length=50), nullable=False),
        sa.Column("planning_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("statut_justificatif", sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["student_matricule"], ["students.matricule"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["planning_session_id"], ["planning_sessions.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_absences_student_matricule",
        "absences", ["student_matricule"], unique=False,
    )
    op.create_index(
        "ix_absences_planning_session_id",
        "absences", ["planning_session_id"], unique=False,
    )
