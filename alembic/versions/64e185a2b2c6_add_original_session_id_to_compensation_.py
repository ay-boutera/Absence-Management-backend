"""add_original_session_id_to_compensation_requests

Revision ID: 64e185a2b2c6
Revises: d1e2f3a4b5c6
Create Date: 2026-06-03 00:58:50.485827

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '64e185a2b2c6'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('compensation_requests', sa.Column('original_session_id', sa.UUID(), nullable=True))
    op.create_index('ix_compensation_requests_original_session_id', 'compensation_requests', ['original_session_id'], unique=False)
    op.create_foreign_key(
        'fk_compensation_requests_original_session_id',
        'compensation_requests', 'sessions',
        ['original_session_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_compensation_requests_original_session_id', 'compensation_requests', type_='foreignkey')
    op.drop_index('ix_compensation_requests_original_session_id', table_name='compensation_requests')
    op.drop_column('compensation_requests', 'original_session_id')
