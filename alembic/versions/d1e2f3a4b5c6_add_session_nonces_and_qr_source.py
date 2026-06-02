"""Add session_nonces table and QR absence source

Revision ID: d1e2f3a4b5c6
Revises: c2d3e4f5a6b7
Create Date: 2026-06-02 00:00:00.000000

Changes:
  - Add 'QR' value to absencesourceenum
  - New table: session_nonces (one active nonce per session, 30-second TTL)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ADD VALUE cannot run inside a transaction on PG < 12.
    # On PG 12+ it is allowed; for safety we commit first.
    connection = op.get_bind()
    connection.execute(sa.text("COMMIT"))
    connection.execute(sa.text("ALTER TYPE absencesourceenum ADD VALUE IF NOT EXISTS 'QR'"))
    connection.execute(sa.text("BEGIN"))

    op.create_table(
        "session_nonces",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nonce", sa.String(10), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("session_id", name="uq_session_nonces_session_id"),
    )
    op.create_index("ix_session_nonces_session_id", "session_nonces", ["session_id"])
    op.create_index("ix_session_nonces_expires_at", "session_nonces", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_session_nonces_expires_at", table_name="session_nonces")
    op.drop_index("ix_session_nonces_session_id", table_name="session_nonces")
    op.drop_table("session_nonces")
    # Enum values cannot be removed in PostgreSQL — downgrade leaves 'QR' in the enum.
