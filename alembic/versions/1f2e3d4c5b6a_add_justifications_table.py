"""Add justifications table

Revision ID: 1f2e3d4c5b6a
Revises: a2b3c4d5e6f7
Create Date: 2026-05-25 20:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "1f2e3d4c5b6a"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


justification_scope_enum = sa.Enum(
    "absence",
    "session",
    "range",
    name="justificationscopetype",
)

justification_status_enum = sa.Enum(
    "pending",
    "approved",
    "rejected",
    name="justificationstatus",
)


def upgrade() -> None:
    bind = op.get_bind()
    justification_scope_enum.create(bind, checkfirst=True)
    justification_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "justifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_type", justification_scope_enum, nullable=False),
        sa.Column("absence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("document_url", sa.Text(), nullable=False),
        sa.Column("document_name", sa.Text(), nullable=False),
        sa.Column("cloudinary_public_id", sa.Text(), nullable=False),
        sa.Column("status", justification_status_enum, nullable=False, server_default="pending"),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["absence_id"], ["absences.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("student_id", "absence_id", name="uq_justification_student_absence"),
        sa.UniqueConstraint("student_id", "session_id", name="uq_justification_student_session"),
    )

    op.create_index("ix_justifications_student_id", "justifications", ["student_id"], unique=False)
    op.create_index("ix_justifications_absence_id", "justifications", ["absence_id"], unique=False)
    op.create_index("ix_justifications_session_id", "justifications", ["session_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_justifications_session_id", table_name="justifications")
    op.drop_index("ix_justifications_absence_id", table_name="justifications")
    op.drop_index("ix_justifications_student_id", table_name="justifications")
    op.drop_table("justifications")

    bind = op.get_bind()
    justification_status_enum.drop(bind, checkfirst=True)
    justification_scope_enum.drop(bind, checkfirst=True)
