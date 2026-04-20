"""Add Enums to PlanningSession fields

Revision ID: 3a4ad6b49081
Revises: c1d2e3f4a5b6
Create Date: 2026-04-13 23:54:39.848914

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a4ad6b49081'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create the types
    sa.Enum('1CP', '2CP', '1CS', '2CS', '3CS', name='academicyear').create(op.get_bind(), checkfirst=True)
    sa.Enum('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z', name='sectionenum').create(op.get_bind(), checkfirst=True)
    sa.Enum('ISI', 'SIW', 'IASD', 'CyS', name='specialityenum').create(op.get_bind(), checkfirst=True)

    # 2. Alter the columns using the types
    op.alter_column('planning_sessions', 'year',
               existing_type=sa.VARCHAR(length=10),
               type_=sa.Enum('1CP', '2CP', '1CS', '2CS', '3CS', name='academicyear'),
               existing_nullable=False,
               postgresql_using="year::academicyear")
    op.alter_column('planning_sessions', 'section',
               existing_type=sa.VARCHAR(length=5),
               type_=sa.Enum('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z', name='sectionenum'),
               existing_nullable=True,
               postgresql_using="section::sectionenum")
    op.alter_column('planning_sessions', 'speciality',
               existing_type=sa.VARCHAR(length=10),
               type_=sa.Enum('ISI', 'SIW', 'IASD', 'CyS', name='specialityenum'),
               existing_nullable=True,
               postgresql_using="speciality::specialityenum")


def downgrade() -> None:
    # 1. Alter the columns back to VARCHAR
    op.alter_column('planning_sessions', 'speciality',
               existing_type=sa.Enum('ISI', 'SIW', 'IASD', 'CyS', name='specialityenum'),
               type_=sa.VARCHAR(length=10),
               existing_nullable=True)
    op.alter_column('planning_sessions', 'section',
               existing_type=sa.Enum('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z', name='sectionenum'),
               type_=sa.VARCHAR(length=5),
               existing_nullable=True)
    op.alter_column('planning_sessions', 'year',
               existing_type=sa.Enum('1CP', '2CP', '1CS', '2CS', '3CS', name='academicyear'),
               type_=sa.VARCHAR(length=10),
               existing_nullable=False)

    # 2. Drop the types
    sa.Enum('1CP', '2CP', '1CS', '2CS', '3CS', name='academicyear').drop(op.get_bind(), checkfirst=True)
    sa.Enum('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z', name='sectionenum').drop(op.get_bind(), checkfirst=True)
    sa.Enum('ISI', 'SIW', 'IASD', 'CyS', name='specialityenum').drop(op.get_bind(), checkfirst=True)
