import re

with open("app/routers/students.py", "r") as f:
    content = f.read()

start_marker = "async def get_student_profile("
if start_marker in content:
    # 1. Rename the existing function to _get_student_profile_logic and remove decorators from it.
    # Wait, it's easier to just copy the body. Let's find where the body starts.

    # Find the function signature match
    pattern = re.compile(
        r"@router\.get\(\s*\"/students/\{matricule\}\".*?\)\s*async def get_student_profile\(\n    matricule: str,\n    current_user=Depends\(require_role\(UserRole\.ADMIN\)\),\n    db: AsyncSession = Depends\(get_db\),\n\):",
        re.DOTALL
    )
    
    match = pattern.search(content)
    if match:
        original_decorator_and_sig = match.group(0)
        start_index = match.end()
        
        # Everything from start_index to the end of the file or next route is the body.
        # Luckily there's only one route after this: PATCH /students/{student_id}/status
        next_route_marker = "@router.patch(\n    \"/students/{student_id}/status\","
        next_route_index = content.find(next_route_marker)
        
        if next_route_index != -1:
            body = content[start_index:next_route_index]
            
            # The body ends with `\n\n# ── PATCH`
            
            new_logic_func = f"""
async def _get_student_profile_logic(matricule: str, db: AsyncSession) -> StudentProfileOut:
{body}

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
    responses={{
        200: {{"description": "Student profile fetched successfully"}},
        404: {{"description": "Student not found"}},
    }},
)
async def get_my_profile(
    current_user=Depends(require_role(UserRole.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    student_matricule = current_user.student_id
    if not student_matricule:
        raise HTTPException(status_code=400, detail="Student profile missing matricule")
    return await _get_student_profile_logic(student_matricule, db)


{original_decorator_and_sig}
    return await _get_student_profile_logic(matricule, db)


"""
            
            new_content = content[:match.start()] + new_logic_func + content[next_route_index:]
            
            with open("app/routers/students.py", "w") as f:
                f.write(new_content)
            
            print("Rewrite successful!")
        else:
            print("Next route not found.")
    else:
        print("Decorator and sig not found.")

