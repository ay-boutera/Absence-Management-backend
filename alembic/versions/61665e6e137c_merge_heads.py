"""merge_heads

Revision ID: 61665e6e137c
Revises: 6e06560c8e23, 845507976fa6
Create Date: 2026-04-20 19:14:20.581018

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '61665e6e137c'
down_revision: Union[str, None] = ('6e06560c8e23', '845507976fa6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
