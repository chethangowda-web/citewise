"""Outgoing email. Without SMTP settings the message is written to the log instead, which is enough
for local development (the reset link appears in the backend log) but must not be relied on in production."""
import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger("rag.mail")


def send_mail(to: str, subject: str, body: str) -> None:
    """Never raises: a mail outage must not turn a password-reset request into an error that reveals anything."""
    s = get_settings()
    if not s.smtp_host:
        log.warning("mail_not_sent_no_smtp_configured", extra={"to": to, "subject": subject, "body": body})
        return
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException):
        log.exception("mail_send_failed", extra={"to": to, "subject": subject})
