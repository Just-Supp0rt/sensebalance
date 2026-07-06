"""Session tokens (itsdangerous) + magic link tokens (secrets) + Gmail send."""
from __future__ import annotations

import secrets
import smtplib
import logging
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

from itsdangerous import URLSafeTimedSerializer, BadSignature

from app import config

log = logging.getLogger(__name__)

_signer: URLSafeTimedSerializer | None = None


def _get_signer() -> URLSafeTimedSerializer:
    global _signer
    if _signer is None:
        _signer = URLSafeTimedSerializer(config.SECRET_KEY)
    return _signer


def make_session_token(user_id: int) -> str:
    return _get_signer().dumps({"uid": user_id})


def verify_session_token(token: str) -> int | None:
    try:
        data = _get_signer().loads(token, max_age=config.SESSION_TTL_DAYS * 86400)
        return data["uid"]
    except (BadSignature, Exception):
        return None


KIOSK_TTL_SECONDS = 2 * 3600


def make_kiosk_token(admin_id: int) -> str:
    return _get_signer().dumps({"kiosk_by": admin_id}, salt="kiosk")


def verify_kiosk_token(token: str) -> int | None:
    """Returns the admin id that started the kiosk, or None."""
    try:
        data = _get_signer().loads(token, salt="kiosk", max_age=KIOSK_TTL_SECONDS)
        return data["kiosk_by"]
    except (BadSignature, Exception):
        return None


def generate_magic_token() -> str:
    return secrets.token_urlsafe(32)


def magic_token_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=config.MAGIC_LINK_TTL_MINUTES)).isoformat()


def send_magic_link(to_email: str, link: str):
    if not config.GMAIL_USER or not config.GMAIL_APP_PASSWORD:
        log.warning("Gmail not configured — skipping magic link send. Link: %s", link)
        return
    msg = MIMEText(
        f"Klikněte na odkaz pro přihlášení do Sense Balance:\n\n{link}\n\n"
        f"Odkaz je platný {config.MAGIC_LINK_TTL_MINUTES} minut.\n\n"
        f"Pokud jste o odkaz nepožádali, ignorujte tento e-mail.",
        "plain",
        "utf-8",
    )
    msg["Subject"] = "Přihlášení do Sense Balance"
    msg["From"] = config.GMAIL_USER
    msg["To"] = to_email
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
            s.sendmail(config.GMAIL_USER, [to_email], msg.as_string())
        log.info("Magic link sent to %s", to_email)
    except Exception:
        log.exception("Failed to send magic link to %s", to_email)
