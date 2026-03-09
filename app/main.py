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
    http://localhost:8000/api/v1/docs ( hna talgo documentation b3d ma diro run l server b3dha dir run l frontend ).
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.db import engine, Base
from app.routers import auth, users


# ── Rate Limiter ──────────────────────────────────────────────────────────────
# Protects auth endpoints against brute-force attacks (ENF-06).
# Uses the client's IP address as the rate-limit key.
limiter = Limiter(key_func=get_remote_address)


# ── Startup / Shutdown ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Code before 'yield' runs at startup.
    Code after 'yield' runs at shutdown.

    In development: create tables automatically.
    In production: use Alembic migrations (never auto-create).
    """
    if settings.DEBUG:
        # Auto-create tables in dev — in production, run: alembic upgrade head
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("✅ Dev tables created/verified")

    yield  # Application runs here

    # Shutdown: close the DB connection pool
    await engine.dispose()
    print("✅ Database connection pool closed")


# ── FastAPI App Instance ──────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    description="Backend API for the ESI-SBA Absence Management System",
    version="1.0.0",
    docs_url="/api/v1/docs",  # Swagger UI
    redoc_url="/api/v1/redoc",  # ReDoc
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)

# Attach rate limiter to the app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── CORS Middleware ───────────────────────────────────────────────────────────
# CORS controls which origins (frontend URLs) are allowed to call this API.
# allow_credentials=True is REQUIRED when using cookies.
# Without it, the browser will refuse to send cookies cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],  # e.g., "http://localhost:5173"
    allow_credentials=True,  # REQUIRED for HttpOnly cookies
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],  # Must allow X-CSRF-Token
)


# ── Security Headers Middleware ───────────────────────────────────────────────
@app.middleware("http")
async def security_headers(request: Request, call_next):
    """
    Add security headers to every response.
    These headers protect against common browser-based attacks.
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # CSP: only allow scripts from self (ENF-08 equivalent)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: https://fastapi.tiangolo.com; "
        "connect-src 'self';"
    )
    return response


# ── Routers ───────────────────────────────────────────────────────────────────
# All routes are prefixed with /api/v1 for versioning.
app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")


# ── Health Check ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health_check():
    """Simple endpoint to verify the API is running. Used by monitoring tools."""
    return {"status": "ok", "service": settings.APP_NAME}


# ── Root ──────────────────────────────────────────────────────────────────────
@app.get("/", tags=["System"])
async def root():
    return {
        "message": "AMS API is running",
        "docs": "/api/v1/docs",
    }
