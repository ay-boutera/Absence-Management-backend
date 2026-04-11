from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from dotenv import load_dotenv
load_dotenv()

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


    # ── Google OAuth2 ──────────────────────────────────────────────────────────
    #   Get these from: https://console.cloud.google.com/apis/credentials
    #   Add Authorized redirect URI:
    #      http://localhost:8000/api/v1/auth/google/callback   (dev)
    #      https://your-domain.com/api/v1/auth/google/callback (prod)
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"

    # ── ESI-SBA Email Domain ───────────────────────────────────────────────────
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
    FRONTEND_URL: str = "http://localhost:5173"
    CORS_ALLOW_ALL: bool = False

    # ── Password Reset Token ───────────────────────────────────────────────────
    RESET_TOKEN_EXPIRE_MINUTES: int = 30

    # ── Session Inactivity ────────────────────────────────────────────────────
    SESSION_INACTIVITY_MINUTES: int = 30

    # ── Environment ───────────────────────────────────────────────────────────
    ENVIRONMENT: str = "production"  # "development" or "production"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
