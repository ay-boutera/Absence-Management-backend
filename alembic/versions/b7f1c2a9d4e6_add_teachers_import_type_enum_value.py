"""add teachers import type enum value

Revision ID: b7f1c2a9d4e6
Revises: a835217b156f
Create Date: 2026-04-13 12:40:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b7f1c2a9d4e6"
down_revision: Union[str, None] = "a835217b156f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE importtype ADD VALUE IF NOT EXISTS 'TEACHERS'")


def downgrade() -> None:
    op.execute("UPDATE import_history SET import_type = 'STUDENTS' WHERE import_type = 'TEACHERS'")
    op.execute("ALTER TYPE importtype RENAME TO importtype_old")
    op.execute("CREATE TYPE importtype AS ENUM ('STUDENTS', 'PLANNING')")
    op.execute(
        """
        ALTER TABLE import_history
        ALTER COLUMN import_type
        TYPE importtype
        USING import_type::text::importtype
        """
    )
    op.execute("DROP TYPE importtype_old")
