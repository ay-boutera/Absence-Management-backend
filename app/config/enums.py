from enum import Enum as PyEnum


# ── Role Enum ──────────────────────────────────────────────────────────────────
class UserRole(str, PyEnum):
    ADMIN = "admin"
    TEACHER = "teacher"
    STUDENT = "student"
