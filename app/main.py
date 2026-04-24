"""
main.py — FastAPI Application Entry Point
==========================================

Run with:
    uvicorn app.main:app --reload
    http://localhost:8000/api/v1/docs
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from app.middlewares.security import security_headers
from app.config import settings
from app.db import engine
from app.routers import (
    absences,
    auth,
    exports,
    justifications,
    schedule,
    sessions,
)
from app.routers.accounts import router as accounts_router
from app.routers.imports import router as imports_router
from app.routers.students import router as students_router
from app.services.email_service import log_smtp_health_check

logger = logging.getLogger(__name__)

CRITICAL_TABLES = (
    "admins",
    "teachers",
    "student_users",
    "audit_logs",
    "password_reset_tokens",
    "import_history",
    "import_export_logs",
    "sessions",
    "absences",
)


def _get_alembic_head_revision() -> str:
    project_root = Path(__file__).resolve().parents[1]
    alembic_ini_path = project_root / "alembic.ini"
    alembic_config = Config(str(alembic_ini_path))
    alembic_config.set_main_option("script_location", str(project_root / "alembic"))
    script_directory = ScriptDirectory.from_config(alembic_config)
    return script_directory.get_current_head()


async def _get_database_revision() -> str | None:
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            return result.scalar_one_or_none()
    except SQLAlchemyError as exc:
        logger.exception(
            "Database revision check failed. Run `alembic upgrade head` before starting the API."
        )
        raise RuntimeError("Database revision check failed") from exc


async def _get_critical_tables_status() -> dict[str, bool]:
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")
            )
            existing_tables = {row[0] for row in result.fetchall()}
    except SQLAlchemyError as exc:
        logger.exception("Failed to inspect database tables for health check.")
        raise RuntimeError("Database table inspection failed") from exc

    return {table_name: table_name in existing_tables for table_name in CRITICAL_TABLES}


async def _assert_database_revision_is_current() -> None:
    expected_revision = _get_alembic_head_revision()
    current_revision = await _get_database_revision()

    if current_revision != expected_revision:
        logger.error(
            "Database revision mismatch: database=%s expected=%s. Run `alembic upgrade head`.",
            current_revision,
            expected_revision,
        )
        raise RuntimeError(
            "Database revision mismatch. Run `alembic upgrade head` before starting the API."
        )


# ── Rate Limiter ───────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)


# ── Startup / Shutdown ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await _assert_database_revision_is_current()
    await log_smtp_health_check()
    yield
    await engine.dispose()
    print("✅ Database connection pool closed")


# ── FastAPI App Instance ───────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    description="Backend API for the ESI-SBA Absence Management System",
    version="1.0.0",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Session Middleware (OAuth CSRF protection) ─────────────────────────────────
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    same_site="none" if settings.ENVIRONMENT == "production" else "lax",
    https_only=settings.ENVIRONMENT == "production",
    max_age=600,
)


# ── CORS Middleware ────────────────────────────────────────────────────────────
cors_kwargs = {
    "allow_credentials": True,
    "allow_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    "allow_headers": ["Content-Type", "X-CSRF-Token", "Authorization"],
}

if settings.CORS_ALLOW_ALL:
    cors_kwargs["allow_origins"] = []
    cors_kwargs["allow_origin_regex"] = ".*"
else:
    cors_kwargs["allow_origins"] = [settings.FRONTEND_URL]
    cors_kwargs["allow_origin_regex"] = r"http://localhost(:\d+)?"

app.add_middleware(CORSMiddleware, **cors_kwargs)


# ── Security Headers ───────────────────────────────────────────────────────────
app.middleware("http")(security_headers)


# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(auth.router,       prefix="/api/v1")
app.include_router(accounts_router,   prefix="/api/v1")  # /accounts/*
app.include_router(imports_router,    prefix="/api/v1")  # /import/*
app.include_router(exports.router,    prefix="/api/v1")  # /export/*
app.include_router(schedule.router,   prefix="/api/v1")  # /planning/my-schedule
app.include_router(sessions.router,   prefix="/api/v1")  # /sessions/*
app.include_router(absences.router,        prefix="/api/v1")  # /absences/*
app.include_router(justifications.router,  prefix="/api/v1")  # /justifications/*
app.include_router(students_router,        prefix="/api/v1")  # /students/*


# ── Health Checks ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok", "service": settings.APP_NAME}


@app.get("/health/db", tags=["System"])
async def database_health_check():
    expected_revision = _get_alembic_head_revision()

    try:
        current_revision = await _get_database_revision()
    except RuntimeError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "service": settings.APP_NAME,
                "error": str(exc),
                "expected_revision": expected_revision,
                "current_revision": None,
            },
        )

    if current_revision != expected_revision:
        return JSONResponse(
            status_code=503,
            content={
                "status": "out_of_sync",
                "service": settings.APP_NAME,
                "expected_revision": expected_revision,
                "current_revision": current_revision,
                "hint": "Run `alembic upgrade head`.",
            },
        )

    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "expected_revision": expected_revision,
        "current_revision": current_revision,
    }


@app.get("/health/db/tables", tags=["System"])
async def database_tables_health_check():
    try:
        table_status = await _get_critical_tables_status()
    except RuntimeError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "service": settings.APP_NAME,
                "error": str(exc),
                "tables": None,
            },
        )

    missing_tables = [name for name, exists in table_status.items() if not exists]
    if missing_tables:
        return JSONResponse(
            status_code=503,
            content={
                "status": "missing_tables",
                "service": settings.APP_NAME,
                "tables": table_status,
                "missing": missing_tables,
                "hint": "Run `alembic upgrade head`.",
            },
        )

    return {"status": "ok", "service": settings.APP_NAME, "tables": table_status}


@app.get("/", tags=["System"])
async def root():
    return {"message": "AMS API is running", "docs": "/api/v1/docs"}
