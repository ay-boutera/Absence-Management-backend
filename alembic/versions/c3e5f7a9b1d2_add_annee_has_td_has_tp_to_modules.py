"""add annee, has_td, has_tp to modules

Revision ID: c3e5f7a9b1d2
Revises: b2d4f6a8c0e1
Create Date: 2026-04-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3e5f7a9b1d2"
down_revision: Union[str, None] = "b2d4f6a8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("modules", sa.Column("annee", sa.String(length=10), nullable=True))
    op.add_column(
        "modules",
        sa.Column("has_td", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "modules",
        sa.Column("has_tp", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("modules", "has_tp")
    op.drop_column("modules", "has_td")
    op.drop_column("modules", "annee")
