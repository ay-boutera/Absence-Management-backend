"""merge_dual_heads

Revision ID: 6e06560c8e23
Revises: c3e5f7a9b1d2, e9c1a7b2d4f6
Create Date: 2026-04-16 17:15:37.137211

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6e06560c8e23'
down_revision: Union[str, None] = ('c3e5f7a9b1d2', 'e9c1a7b2d4f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
