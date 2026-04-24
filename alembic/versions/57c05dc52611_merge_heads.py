"""merge heads

Revision ID: 57c05dc52611
Revises: 61665e6e137c, a2b3c4d5e6f7
Create Date: 2026-04-23 23:38:38.852426

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '57c05dc52611'
down_revision: Union[str, None] = ('61665e6e137c', 'a2b3c4d5e6f7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
