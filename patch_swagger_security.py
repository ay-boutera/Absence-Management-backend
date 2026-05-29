import re
with open("app/helpers/permissions.py", "r") as f:
    content = f.read()

if "fastapi.security" not in content:
    content = content.replace("from fastapi import Depends, HTTPException, Request, status", "from fastapi import Depends, HTTPException, Request, status\nfrom fastapi.security import HTTPBearer, HTTPAuthorizationCredentials\n\nbearer_scheme = HTTPBearer(auto_error=False)\n")
    content = content.replace("async def get_current_user_bearer(\n    request: Request,\n    db: AsyncSession = Depends(get_db),\n) -> RoleUser:", "async def get_current_user_bearer(\n    request: Request,\n    db: AsyncSession = Depends(get_db),\n    token_auth: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)\n) -> RoleUser:")

    with open("app/helpers/permissions.py", "w") as f:
        f.write(content)
    print("Patched!")
else:
    print("Already patched or imported.")
