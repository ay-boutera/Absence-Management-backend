"""Sprint 4 — justifications table

Revision ID: d1e2f3a4b5c6
Revises: 57c05dc52611
Create Date: 2026-04-24 00:00:00.000000

Changes:
  - Create justificationstatusenum type
  - Create `justifications` table
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "57c05dc52611"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE justificationstatusenum AS ENUM
                ('non_justifiee', 'en_attente', 'justifiee', 'rejetee');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS justifications (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            absence_id      UUID        NOT NULL UNIQUE
                                REFERENCES absences(id) ON DELETE CASCADE,
            student_matricule VARCHAR(50) NOT NULL
                                REFERENCES students(matricule) ON DELETE RESTRICT,
            file_path       VARCHAR(500) NOT NULL,
            file_name       VARCHAR(255) NOT NULL,
            file_type       VARCHAR(10)  NOT NULL,
            file_size       INTEGER      NOT NULL,
            status          justificationstatusenum NOT NULL DEFAULT 'en_attente',
            admin_comment   TEXT,
            deadline        TIMESTAMPTZ  NOT NULL,
            submitted_at    TIMESTAMPTZ  NOT NULL,
            reviewed_at     TIMESTAMPTZ,
            reviewed_by     UUID REFERENCES admins(id) ON DELETE SET NULL
        );
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_justifications_student_matricule
            ON justifications(student_matricule);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS justifications;")
    op.execute("DROP TYPE IF EXISTS justificationstatusenum;")
