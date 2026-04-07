"""rbac specialized user models

Revision ID: a1c2d3e4f5g6
Revises: 9d8a7c6b5e4f
Create Date: 2026-04-07 12:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a1c2d3e4f5g6"
down_revision: Union[str, None] = "9d8a7c6b5e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


import_export_action_enum = postgresql.ENUM(
    "IMPORT",
    "EXPORT",
    name="importexportaction",
    create_type=False,
)
import_export_file_type_enum = postgresql.ENUM(
    "CSV",
    "PDF",
    "EXCEL",
    name="importexportfiletype",
    create_type=False,
)
import_export_data_type_enum = postgresql.ENUM(
    "STUDENTS",
    "ATTENDANCE",
    "SCHEDULE",
    "JUSTIFICATIONS",
    name="importexportdatatype",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("users", sa.Column("phone", sa.String(length=20), nullable=True))

    op.create_table(
        "admin_users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("department", sa.String(length=100), nullable=False),
        sa.Column("admin_level", sa.String(length=20), nullable=False),
        sa.Column("can_import_data", sa.Boolean(), nullable=False),
        sa.Column("can_export_data", sa.Boolean(), nullable=False),
        sa.Column("can_manage_users", sa.Boolean(), nullable=False),
        sa.Column("can_manage_system_config", sa.Boolean(), nullable=False),
        sa.Column("can_view_audit_logs", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_admin_users_user_id"), "admin_users", ["user_id"], unique=False)

    op.create_table(
        "teacher_users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("employee_id", sa.String(length=50), nullable=True),
        sa.Column("specialization", sa.String(length=200), nullable=True),
        sa.Column("can_mark_attendance", sa.Boolean(), nullable=False),
        sa.Column("can_export_data", sa.Boolean(), nullable=False),
        sa.Column("can_correct_attendance", sa.Boolean(), nullable=False),
        sa.Column("correction_window_minutes", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_teacher_users_employee_id"), "teacher_users", ["employee_id"], unique=True)
    op.create_index(op.f("ix_teacher_users_user_id"), "teacher_users", ["user_id"], unique=False)

    op.add_column(
        "student_profiles",
        sa.Column("can_submit_justifications", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "student_profiles",
        sa.Column("can_view_attendance", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "student_profiles",
        sa.Column("can_confirm_rattrapage", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "student_profiles",
        sa.Column("is_enrolled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    import_export_action_enum.create(bind, checkfirst=True)
    import_export_file_type_enum.create(bind, checkfirst=True)
    import_export_data_type_enum.create(bind, checkfirst=True)

    op.create_table(
        "import_export_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("performed_by_id", sa.UUID(), nullable=True),
        sa.Column("action", import_export_action_enum, nullable=False),
        sa.Column("file_type", import_export_file_type_enum, nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("data_type", import_export_data_type_enum, nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("error_details", sa.JSON(), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["performed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_import_export_logs_performed_by_id"),
        "import_export_logs",
        ["performed_by_id"],
        unique=False,
    )

    op.alter_column("student_profiles", "can_submit_justifications", server_default=None)
    op.alter_column("student_profiles", "can_view_attendance", server_default=None)
    op.alter_column("student_profiles", "can_confirm_rattrapage", server_default=None)
    op.alter_column("student_profiles", "is_enrolled", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_import_export_logs_performed_by_id"), table_name="import_export_logs")
    op.drop_table("import_export_logs")

    bind = op.get_bind()
    import_export_data_type_enum.drop(bind, checkfirst=True)
    import_export_file_type_enum.drop(bind, checkfirst=True)
    import_export_action_enum.drop(bind, checkfirst=True)

    op.drop_column("student_profiles", "is_enrolled")
    op.drop_column("student_profiles", "can_confirm_rattrapage")
    op.drop_column("student_profiles", "can_view_attendance")
    op.drop_column("student_profiles", "can_submit_justifications")

    op.drop_index(op.f("ix_teacher_users_user_id"), table_name="teacher_users")
    op.drop_index(op.f("ix_teacher_users_employee_id"), table_name="teacher_users")
    op.drop_table("teacher_users")

    op.drop_index(op.f("ix_admin_users_user_id"), table_name="admin_users")
    op.drop_table("admin_users")

    op.drop_column("users", "phone")
