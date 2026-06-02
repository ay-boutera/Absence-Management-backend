"""Add compensation_requests table

Revision ID: c2d3e4f5a6b7
Revises: e6f7a8b9c0d1
Create Date: 2026-06-02 00:00:00.000000

Changes:
  - New enum: compensationrequeststatus (PENDING, APPROVED, REJECTED)
  - New table: compensation_requests
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS_ENUM = postgresql.ENUM(
    "PENDING", "APPROVED", "REJECTED",
    name="compensationrequeststatus",
    create_type=False,
)


def upgrade() -> None:
    conn = op.get_bind()

    # Create the enum type if it does not already exist
    _STATUS_ENUM.create(conn, checkfirst=True)

    inspector = sa.inspect(conn)
    if "compensation_requests" not in inspector.get_table_names():
        op.create_table(
            "compensation_requests",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "session_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column("student_matricule", sa.String(50), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "PENDING", "APPROVED", "REJECTED",
                    name="compensationrequeststatus",
                    create_type=False,
                ),
                nullable=False,
                server_default="PENDING",
            ),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), nullable=False
            ),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["session_id"], ["sessions.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["student_matricule"], ["students.matricule"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_compensation_requests_session_id",
            "compensation_requests",
            ["session_id"],
        )
        op.create_index(
            "ix_compensation_requests_student_matricule",
            "compensation_requests",
            ["student_matricule"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_compensation_requests_student_matricule",
        table_name="compensation_requests",
    )
    op.drop_index(
        "ix_compensation_requests_session_id",
        table_name="compensation_requests",
    )
    op.drop_table("compensation_requests")
    conn = op.get_bind()
    _STATUS_ENUM.drop(conn, checkfirst=True)
