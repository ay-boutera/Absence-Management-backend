with open("app/routers/students.py", "r") as f:
    text = f.read()

# 1. We want to rename `async def get_student_profile(\n    matricule: str,\n    current_user=Depends(require_role(UserRole.ADMIN)),\n    db: AsyncSession = Depends(get_db),\n):`
# Into a helper function `async def _get_student_profile_logic(matricule: str, db: AsyncSession) -> StudentProfileOut:`
old_sig = """async def get_student_profile(
    matricule: str,
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):"""
new_sig = """async def _get_student_profile_logic(
    matricule: str,
    db: AsyncSession,
) -> StudentProfileOut:"""

text = text.replace(old_sig, new_sig)

# 2. But we still need the original endpoint AND the new endpoint.
# We will inject them right before `# ── PATCH /students/{id}/status`

new_endpoints = """
@router.get(
    "/students/me",
    response_model=StudentProfileOut,
    summary="Get my student profile and absence summary",
    description=\"\"\"
Returns a complete profile for the authenticated student.

**Includes:**
- Personal and academic info.
- Attendance summary: total_absences, attendance_rate.
- Per-module attendance summary: module_attendance[].
- Full absence history.

**Auth:** Student only.
\"\"\",
    responses={
        200: {"description": "Student profile fetched successfully"},
        404: {"description": "Student not found"},
    },
)
async def get_my_profile(
    current_user=Depends(require_role(UserRole.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    student_matricule = current_user.student_id
    if not student_matricule:
        raise HTTPException(status_code=400, detail="Student profile missing matricule")
    return await _get_student_profile_logic(student_matricule, db)


@router.get(
    "/students/{matricule}",
    response_model=StudentProfileOut,
    summary="Full student profile with absence history (Admin)",
    description=\"\"\"
Returns a complete profile for a single student identified by their **matricule**.

**Includes:**
- Personal info (name, email, phone, avatar)
- Academic info (year, group, filière, status)
- Account info (avatar, creation date, last login) — merged from `student_users` if a linked account exists
- Attendance summary: `total_absences`, `total_sessions`, `attendance_rate` (%)
- Per-module attendance summary: `module_attendance[]` with `absences` for each module
- Full absence history: per-session records with date, module, teacher, justification status

**Input:**
- Path parameter `matricule` (string), example: `232332029109`

**Output:**
- Full student profile object including:
  - student identity and academic data
  - global attendance KPIs
  - `module_attendance` with per-module absence counters
  - `absence_history` with `is_own_group` and `is_cross_session`

**Auth:** Admin only.
\"\"\",
    responses={
        200: {"description": "Student profile fetched successfully"},
        404: {"description": "Student not found"},
    },
)
async def get_student_profile(
    matricule: str,
    current_user=Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    return await _get_student_profile_logic(matricule, db)

"""

# We need to remove the original `@router.get("/students/{matricule}"...)` decorator which is above the original `async def get_student_profile`
# Let's find it.
decorator_start = '@router.get(\n    "/students/{matricule}",'
# I'll just remove the whole decorator block manually since it's hard to regex blindly.

text = text.replace(decorator_start, "# DECORATOR_REMOVED\n" + decorator_start)
import re
text = re.sub(r'# DECORATOR_REMOVED.*?async def _get_student_profile_logic', 'async def _get_student_profile_logic', text, flags=re.DOTALL)


# Now insert the new endpoints before the PATCH endpoint
patch_marker = "# ── PATCH /students/{id}/status ───────────────────────────────────────────────"
text = text.replace(patch_marker, new_endpoints + "\n" + patch_marker)

with open("app/routers/students.py", "w") as f:
    f.write(text)

