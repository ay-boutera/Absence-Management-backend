from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str

    # ── JWT ───────────────────────────────────────────────────────────────────
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15  # short-lived access token
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7  # long-lived refresh token

    # ── CSRF ──────────────────────────────────────────────────────────────────
    CSRF_SECRET_KEY: str

    # ── Redis (token blacklist) ────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Google OAuth2 ──────────────────────────────────────────────────────────
    # Get these from: https://console.cloud.google.com/apis/credentials
    # Steps:
    #   1. Create a project → Enable "Google People API"
    #   2. OAuth 2.0 Credentials → Web Application
    #   3. Add Authorized redirect URI:
    #      http://localhost:8000/api/v1/auth/google/callback   (dev)
    #      https://your-domain.com/api/v1/auth/google/callback (prod)
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"

    # ── ESI-SBA Email Domain ───────────────────────────────────────────────────
    # Both login methods enforce this domain.
    # Format: firstletter.lastname@esi-sba.dz
    # Example: i.brahmi@esi-sba.dz  (Ilyes Brahmi)
    ALLOWED_EMAIL_DOMAIN: str = "esi-sba.dz"

    # ── Email (password reset for credential users) ────────────────────────────
    MAIL_USERNAME: str
    MAIL_PASSWORD: str
    MAIL_FROM: str
    MAIL_PORT: int = 587
    MAIL_SERVER: str
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False

    # ── App ───────────────────────────────────────────────────────────────────
    APP_NAME: str = "AMS - Absence Management System"
    DEBUG: bool = False
    FRONTEND_URL: str = "http://localhost:5173"

    # ── Password Reset Token ───────────────────────────────────────────────────
    RESET_TOKEN_EXPIRE_MINUTES: int = 30

    # ── Session Inactivity ────────────────────────────────────────────────────
    SESSION_INACTIVITY_MINUTES: int = 30

    # ── Environment ───────────────────────────────────────────────────────────
    ENVIRONMENT: str = "development"  # "development" or "production"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
