"""add subjects and groups to teacher_users, add TEACHERS to importexportdatatype

Revision ID: b2d4f6a8c0e1
Revises: a835217b156f
Create Date: 2026-04-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b2d4f6a8c0e1"
down_revision: Union[str, None] = "a835217b156f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "teacher_users" in inspector.get_table_names():
        cols = [c["name"] for c in inspector.get_columns("teacher_users")]
        if "subjects" not in cols:
            op.add_column("teacher_users", sa.Column("subjects", sa.Text(), nullable=True))
        if "groups" not in cols:
            op.add_column("teacher_users", sa.Column("groups", sa.Text(), nullable=True))

    op.execute("ALTER TYPE importexportdatatype ADD VALUE IF NOT EXISTS 'teachers'")


def downgrade() -> None:
    op.drop_column("teacher_users", "groups")
    op.drop_column("teacher_users", "subjects")
    # Note: removing an enum value from PostgreSQL requires recreating the type;
    # downgrade leaves the 'teachers' enum value in place to avoid complexity.
