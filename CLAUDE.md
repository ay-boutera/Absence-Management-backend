# AMS — Absence Management System

## Stack
- FastAPI, SQLAlchemy, PostgreSQL, Alembic, Pydantic v2
- Hosted on Render

## Rules
- Always read existing files before writing new ones
- Follow existing router/schema/model patterns
- Never modify alembic_version table
- Use Pydantic v2 syntax (model_config, not class Config)
- 5CS: S2 is internship — block planning imports for that semester


After that, the full notification flow is live:
  - Student submits justification → all admins get justification_submitted notification via WebSocket + inbox
  - Admin approves → student gets justification_approved + absences marked as justified (no longer count)
  - Admin rejects → student gets justification_rejected
  - Teacher records 3rd unexcused absence in a module → student gets absence_warning
  - Teacher records 5th unexcused absence in a module → student gets absence_exclusion
  - Flutter app connects via ws://<host>/api/v1/ws/notifications?token=<jwt> to receive all of the above in real time
