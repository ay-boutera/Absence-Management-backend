with open("app/helpers/permissions.py", "r") as f:
    content = f.read()

content = content.replace("async def get_current_user(\n    request: Request,\n    db: AsyncSession = Depends(get_db),\n) -> RoleUser:", "async def get_current_user(\n    request: Request,\n    db: AsyncSession = Depends(get_db),\n    token_auth: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)\n) -> RoleUser:")

with open("app/helpers/permissions.py", "w") as f:
    f.write(content)
print("Patched get_current_user!")
