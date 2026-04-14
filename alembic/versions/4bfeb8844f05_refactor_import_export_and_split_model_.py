"""refactor import-export and split model files

Revision ID: 4bfeb8844f05
Revises: 3a4ad6b49081
Create Date: 2026-04-14 22:13:45.676619

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4bfeb8844f05'
down_revision: Union[str, None] = '3a4ad6b49081'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
