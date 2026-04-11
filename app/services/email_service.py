import asyncio
import logging
import ssl
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from app.config.settings import settings

logger = logging.getLogger(__name__)

conf = ConnectionConfig(
    MAIL_USERNAME=settings.MAIL_USERNAME,
    MAIL_PASSWORD=settings.MAIL_PASSWORD,
    MAIL_FROM=settings.MAIL_FROM,
    MAIL_PORT=settings.MAIL_PORT,
    MAIL_SERVER=settings.MAIL_SERVER,
    MAIL_STARTTLS=settings.MAIL_STARTTLS,
    MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True,
)


async def log_smtp_health_check() -> None:
    """
    Log SMTP configuration/connectivity status at startup.
    This is a diagnostics helper and must never raise.
    """
    missing_keys = [
        key
        for key, value in {
            "MAIL_USERNAME": settings.MAIL_USERNAME,
            "MAIL_PASSWORD": settings.MAIL_PASSWORD,
            "MAIL_FROM": settings.MAIL_FROM,
            "MAIL_SERVER": settings.MAIL_SERVER,
        }.items()
        if not value
    ]

    if missing_keys:
        logger.error(
            "SMTP health check failed: missing required env vars: %s",
            ", ".join(missing_keys),
        )
        return

    logger.info(
        "SMTP health check: server=%s port=%s starttls=%s ssl_tls=%s from=%s",
        settings.MAIL_SERVER,
        settings.MAIL_PORT,
        settings.MAIL_STARTTLS,
        settings.MAIL_SSL_TLS,
        settings.MAIL_FROM,
    )

    try:
        ssl_context = ssl.create_default_context() if settings.MAIL_SSL_TLS else None
        connect = asyncio.open_connection(
            host=settings.MAIL_SERVER,
            port=settings.MAIL_PORT,
            ssl=ssl_context,
        )
        reader, writer = await asyncio.wait_for(connect, timeout=10)
        writer.close()
        await writer.wait_closed()
        logger.info("SMTP health check passed: SMTP server is reachable.")
    except Exception as exc:
        logger.error("SMTP health check failed: %s", str(exc))


async def send_password_reset_email(email: str, full_name: str, token: str) -> bool:
    """
    Send a password reset email to the user.
    """
    reset_link = f"{settings.FRONTEND_URL}/reset-password?token={token}"

    html = f"""
    <p>Hi {full_name},</p>
    <p>You requested a password reset. Please click the link below to set a new password:</p>
    <p><a href="{reset_link}">{reset_link}</a></p>
    <p>This link will expire in {settings.RESET_TOKEN_EXPIRE_MINUTES} minutes.</p>
    <p>If you did not request this, please ignore this email.</p>
    """

    message = MessageSchema(
        subject="Password Reset Request - AMS",
        recipients=[email],
        body=html,
        subtype=MessageType.html,
    )

    fm = FastMail(conf)
    try:
        await fm.send_message(message)
        logger.info(f"Password reset email sent successfully to {email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send password reset email to {email}: {str(e)}")
        return False
