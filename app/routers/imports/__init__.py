from fastapi import APIRouter

from .students import router as students_router
from .teachers import router as teachers_router
from .planning import router as planning_router

router = APIRouter()
router.include_router(students_router)
router.include_router(teachers_router)
router.include_router(planning_router)
