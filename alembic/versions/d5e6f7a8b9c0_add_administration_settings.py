"""Add administration_settings table

Revision ID: d5e6f7a8b9c0
Revises: 1f2e3d4c5b6a
Create Date: 2026-06-01 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "1f2e3d4c5b6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "administration_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("singleton_guard", sa.Boolean(), nullable=False),
        sa.Column("academic_year_label", sa.String(50), nullable=False),
        sa.Column("current_semester", sa.SmallInteger(), nullable=False),
        sa.Column("sem1_start_date", sa.Date(), nullable=True),
        sa.Column("sem1_end_date", sa.Date(), nullable=True),
        sa.Column("sem2_start_date", sa.Date(), nullable=True),
        sa.Column("sem2_end_date", sa.Date(), nullable=True),
        sa.Column("warning_threshold", sa.SmallInteger(), nullable=False),
        sa.Column("max_absences", sa.SmallInteger(), nullable=False),
        sa.Column("justification_deadline_days", sa.SmallInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("singleton_guard", name="uq_administration_settings_singleton"),
    )

    # Seed the one and only settings row with sensible defaults.
    # warning_threshold=3  → student gets a warning after 3 absences in the same module
    # max_absences=5       → student is excluded after 5 absences in the same module
    op.execute(
        """
        INSERT INTO administration_settings (
            id, singleton_guard, academic_year_label, current_semester,
            sem1_start_date, sem1_end_date,
            sem2_start_date, sem2_end_date,
            warning_threshold, max_absences, justification_deadline_days,
            updated_at
        ) VALUES (
            1, TRUE, '2025 \u2013 2026', 1,
            '2025-09-15', '2026-01-31',
            '2026-02-16', '2026-06-30',
            3, 5, 3,
            NOW()
        )
        """
    )


def downgrade() -> None:
    op.drop_table("administration_settings")
