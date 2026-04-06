"""sprint2 import export tables

Revision ID: 9d8a7c6b5e4f
Revises: fb3c5874cd5f
Create Date: 2026-04-07 10:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9d8a7c6b5e4f"
down_revision: Union[str, None] = "fb3c5874cd5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


session_type_enum = sa.Enum("COURS", "TD", "TP", "EXAMEN", name="sessiontype")
import_type_enum = sa.Enum("STUDENTS", "PLANNING", name="importtype")


def upgrade() -> None:
    bind = op.get_bind()
    session_type_enum.create(bind, checkfirst=True)
    import_type_enum.create(bind, checkfirst=True)

    op.create_table(
        "modules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("nom", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_modules_code"), "modules", ["code"], unique=True)

    op.create_table(
        "salles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_salles_code"), "salles", ["code"], unique=True)

    op.create_table(
        "students",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("matricule", sa.String(length=50), nullable=False),
        sa.Column("nom", sa.String(length=120), nullable=False),
        sa.Column("prenom", sa.String(length=120), nullable=False),
        sa.Column("filiere", sa.String(length=120), nullable=False),
        sa.Column("niveau", sa.String(length=50), nullable=False),
        sa.Column("groupe", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_students_matricule"), "students", ["matricule"], unique=True)

    op.create_table(
        "import_history",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("import_type", import_type_enum, nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "planning_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("id_seance", sa.String(length=100), nullable=False),
        sa.Column("code_module", sa.String(length=50), nullable=False),
        sa.Column("type_seance", session_type_enum, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("heure_debut", sa.Time(), nullable=False),
        sa.Column("heure_fin", sa.Time(), nullable=False),
        sa.Column("salle", sa.String(length=50), nullable=False),
        sa.Column("id_enseignant", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["code_module"], ["modules.code"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["id_enseignant"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["salle"], ["salles.code"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_planning_sessions_id_seance"), "planning_sessions", ["id_seance"], unique=True)
    op.create_index(op.f("ix_planning_sessions_id_enseignant"), "planning_sessions", ["id_enseignant"], unique=False)

    op.create_table(
        "absences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("student_matricule", sa.String(length=50), nullable=False),
        sa.Column("planning_session_id", sa.UUID(), nullable=False),
        sa.Column("statut_justificatif", sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(["planning_session_id"], ["planning_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_matricule"], ["students.matricule"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_absences_planning_session_id"), "absences", ["planning_session_id"], unique=False)
    op.create_index(op.f("ix_absences_student_matricule"), "absences", ["student_matricule"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_absences_student_matricule"), table_name="absences")
    op.drop_index(op.f("ix_absences_planning_session_id"), table_name="absences")
    op.drop_table("absences")

    op.drop_index(op.f("ix_planning_sessions_id_enseignant"), table_name="planning_sessions")
    op.drop_index(op.f("ix_planning_sessions_id_seance"), table_name="planning_sessions")
    op.drop_table("planning_sessions")

    op.drop_table("import_history")

    op.drop_index(op.f("ix_students_matricule"), table_name="students")
    op.drop_table("students")

    op.drop_index(op.f("ix_salles_code"), table_name="salles")
    op.drop_table("salles")

    op.drop_index(op.f("ix_modules_code"), table_name="modules")
    op.drop_table("modules")

    bind = op.get_bind()
    import_type_enum.drop(bind, checkfirst=True)
    session_type_enum.drop(bind, checkfirst=True)
