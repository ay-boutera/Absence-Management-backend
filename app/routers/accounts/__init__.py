from fastapi import APIRouter

from .admins import router as admins_router
from .teachers import router as teachers_router
from .students import router as students_router

router = APIRouter()
router.include_router(admins_router)
router.include_router(teachers_router)
router.include_router(students_router)
