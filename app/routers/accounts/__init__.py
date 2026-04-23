from fastapi import APIRouter

from .admins import router as admins_router
from .teachers import router as teachers_router
from .students import router as students_router

router = APIRouter()
# IMPORTANT: teachers and students routers must be included BEFORE admins_router
# because admins_router defines a wildcard GET /{account_id} route that would
# otherwise intercept /accounts/teachers and /accounts/students first.
router.include_router(teachers_router)
router.include_router(students_router)
router.include_router(admins_router)
