"""Sprint 6 — add motif field to absences

Revision ID: f3a4b5c6d7e8
Revises: d1e2f3a4b5c6
Create Date: 2026-04-24 01:00:00.000000

Changes:
  - Add absencemotifEnum type (medical, administratif, familial, sportif_culturel, autre)
  - Add nullable `motif` column to absences table
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE absencemotifEnum AS ENUM
                ('medical', 'administratif', 'familial', 'sportif_culturel', 'autre');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)

    op.execute("""
        ALTER TABLE absences
        ADD COLUMN IF NOT EXISTS motif absencemotifEnum;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE absences DROP COLUMN IF EXISTS motif;")
    op.execute("DROP TYPE IF EXISTS absencemotifenum;")
