import re

with open("app/routers/students.py", "r") as f:
    content = f.read()

start_marker = "async def get_student_profile(\n    matricule: str,\n    current_user=Depends(require_role(UserRole.ADMIN)),\n    db: AsyncSession = Depends(get_db),\n):"
if start_marker in content:
    # Partition the file
    before, sep, after = content.partition(start_marker)
    
    # After contains the function body. Let's find where the get_student_profile ends.
    # We'll just replace `async def get_student_profile(..., db):` with `async def _get_student_profile_logic(matricule: str, db: AsyncSession) -> StudentProfileOut:`
    # And then append the two endpoint wrappers at the top.
    
    new_func_def = "async def _get_student_profile_logic(\n    matricule: str,\n    db: AsyncSession\n) -> StudentProfileOut:"
    
    new_routes = """
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
        200: {
            "description": "Student profile fetched successfully"
        },
        404: {
            "description": "Student not found"
        },
    },
)
async def get_my_profile(
    current_user=Depends(require_role(UserRole.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    # Depending on your setup `current_user` for STUDENT role is an instance of Student
    # and has `student_id` referring to the matricule in `AcademicStudent`.
    student_matricule = current_user.student_id
    if not student_matricule:
        raise HTTPException(status_code=400, detail="Student profile missing matricule")
    return await _get_student_profile_logic(student_matricule, db)


"""

    # Because `@router.get("/students/{matricule}"...)` comes right before `async def get_student_profile`,
    # We should inject our `new_routes` BEFORE the `@router.get("/students/{matricule}"...)`.
    # Let's find the decorator for `/students/{matricule}`
    
    decorator = '@router.get(\n    "/students/{matricule}",'
    before_decorator, sep2, after_decorator = before.partition(decorator)
    
    # Reassemble:
    # 1. Everything before the /students/{matricule} endpoint
    # 2. Our new /students/me endpoint
    # 3. The `_get_student_profile_logic` definition
    # 4. The original /students/{matricule} logic, but modified to just call `_get_student_profile_logic`
    # Wait, the easiest way is to modify the existing `get_student_profile` to rename it to `_get_student_profile_logic`
    # and then add back `get_student_profile` passing down to it... no, the decorator for `get_student_profile` is attached to it!
    pass
