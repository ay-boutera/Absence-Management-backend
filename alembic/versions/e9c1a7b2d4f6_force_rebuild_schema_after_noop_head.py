"""force rebuild schema after noop head

Revision ID: e9c1a7b2d4f6
Revises: 4bfeb8844f05
Create Date: 2026-04-15 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.db.database import Base
from app import models as _models  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = "e9c1a7b2d4f6"
down_revision: Union[str, None] = "4bfeb8844f05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    reflected_metadata = sa.MetaData()
    reflected_metadata.reflect(bind=bind)

    tables_to_drop = [
        table
        for table in reflected_metadata.sorted_tables
        if table.name != "alembic_version"
    ]
    for table in reversed(tables_to_drop):
        table.drop(bind=bind, checkfirst=True)

    if bind.dialect.name == "postgresql":
        enum_names = bind.execute(
            sa.text(
                """
                SELECT t.typname
                FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE t.typtype = 'e' AND n.nspname = current_schema()
                """
            )
        ).scalars().all()

        for enum_name in enum_names:
            bind.execute(sa.text(f'DROP TYPE IF EXISTS "{enum_name}" CASCADE'))

    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()

    reflected_metadata = sa.MetaData()
    reflected_metadata.reflect(bind=bind)

    tables_to_drop = [
        table
        for table in reflected_metadata.sorted_tables
        if table.name != "alembic_version"
    ]
    for table in reversed(tables_to_drop):
        table.drop(bind=bind, checkfirst=True)
