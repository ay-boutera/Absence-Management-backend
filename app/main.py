"""
main.py — FastAPI Application Entry Point
==========================================
This is where the app is assembled:
    1. Create the FastAPI instance
    2. Configure CORS (which origins can call this API)
    3. Add security middleware (rate limiting)
    4. Wire in all routers
    5. Add startup/shutdown hooks

Run with:
    uvicorn app.main:app --reload
    http://localhost:8000/api/v1/docs
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware  # ← NEW

from app.middlewares.security import security_headers
from app.config import settings
from app.db import engine, Base
from app.routers import auth, import_export, users
from app.services.email_service import log_smtp_health_check


# ── Rate Limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)


# ── Startup / Shutdown ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.DEBUG:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("✅ Dev tables created/verified")

    await log_smtp_health_check()

    yield

    await engine.dispose()
    print("✅ Database connection pool closed")


# ── FastAPI App Instance ──────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    description="Backend API for the ESI-SBA Absence Management System",
    version="1.0.0",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)

# Attach rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Session Middleware ────────────────────────────────────────────────────────
# MUST be added BEFORE CORSMiddleware so the session cookie is available
# during the OAuth callback redirect.
# Used exclusively to carry the OAuth `state` token between the two OAuth steps.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,  # reuse your existing secret
    same_site="lax",  # required: allows cookie on cross-site redirect
    https_only=False,  # set True when running on HTTPS in production
    max_age=600,  # 10 minutes — enough time to complete OAuth
)


# ── CORS Middleware ───────────────────────────────────────────────────────────
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


# ── Security Headers Middleware ───────────────────────────────────────────────
app.middleware("http")(security_headers)


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(import_export.router, prefix="/api/v1")


# ── Health Check ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok", "service": settings.APP_NAME}


# ── Root ──────────────────────────────────────────────────────────────────────
@app.get("/", tags=["System"])
async def root():
    return {"message": "AMS API is running", "docs": "/api/v1/docs"}
