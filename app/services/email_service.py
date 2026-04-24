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


async def send_weekly_report_email(
    recipients: list[str],
    stats: dict,
    week_label: str,
) -> bool:
    """Send weekly absence summary to supervisors (US-54)."""
    by_filiere_rows = "".join(
        f"<tr><td>{r['filiere']}</td><td>{r['absences']}</td><td>{r['rate']}%</td></tr>"
        for r in stats.get("by_filiere", [])
    )
    html = f"""
    <h2 style="color:#0d2850">ESI-SBA — Rapport Hebdomadaire des Absences</h2>
    <p><strong>Semaine :</strong> {week_label}</p>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%">
      <tr style="background:#0d2850;color:white">
        <th>Indicateur</th><th>Valeur</th>
      </tr>
      <tr><td>Total enregistrements</td><td>{stats.get("total_records", 0)}</td></tr>
      <tr><td>Total absences</td><td>{stats.get("total_absences", 0)}</td></tr>
      <tr><td>Taux global</td><td>{stats.get("overall_rate", 0)}%</td></tr>
    </table>
    <br/>
    <h3>Répartition par filière</h3>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
      <tr style="background:#1a53a0;color:white"><th>Filière</th><th>Absences</th><th>Taux</th></tr>
      {by_filiere_rows}
    </table>
    <p style="color:gray;font-size:12px">Rapport généré automatiquement par le Système de Gestion des Absences ESI-SBA.</p>
    """
    message = MessageSchema(
        subject=f"Rapport hebdomadaire absences — {week_label}",
        recipients=recipients,
        body=html,
        subtype=MessageType.html,
    )
    fm = FastMail(conf)
    try:
        await fm.send_message(message)
        logger.info("Weekly report sent to %s", recipients)
        return True
    except Exception as exc:
        logger.error("Failed to send weekly report: %s", exc)
        return False


async def send_justification_status_email(
    email: str,
    full_name: str,
    status: str,
    admin_comment: str | None,
    session_info: str,
) -> bool:
    """
    Notify a student that their justification was approved or rejected (US-32).
    """
    if status == "justifiee":
        subject = "Justificatif accepté — AMS"
        status_label = "accepté"
        color = "#16a34a"
        body_extra = "<p>Votre absence a été justifiée avec succès.</p>"
    else:
        subject = "Justificatif rejeté — AMS"
        status_label = "rejeté"
        color = "#dc2626"
        comment_block = (
            f"<p><strong>Motif du refus :</strong> {admin_comment}</p>"
            if admin_comment
            else ""
        )
        body_extra = f"<p>Votre justificatif a été rejeté.</p>{comment_block}"

    html = f"""
    <p>Bonjour {full_name},</p>
    <p>
      Le statut de votre justificatif pour la séance <strong>{session_info}</strong>
      a été mis à jour :
      <span style="color:{color};font-weight:bold">{status_label.upper()}</span>.
    </p>
    {body_extra}
    <p>Connectez-vous à l'application pour consulter les détails.</p>
    """

    message = MessageSchema(
        subject=subject,
        recipients=[email],
        body=html,
        subtype=MessageType.html,
    )
    fm = FastMail(conf)
    try:
        await fm.send_message(message)
        logger.info("Justification status email sent to %s (status=%s)", email, status)
        return True
    except Exception as exc:
        logger.error("Failed to send justification email to %s: %s", email, exc)
        return False


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
