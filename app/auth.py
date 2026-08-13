"""Session tokens (itsdangerous) + magic link tokens (secrets) + Gmail send."""
from __future__ import annotations

import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

from itsdangerous import BadSignature, URLSafeTimedSerializer

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


KIOSK_TTL_SECONDS = 12 * 3600


def make_kiosk_token(admin_id: int) -> str:
    return _get_signer().dumps({"kiosk_by": admin_id}, salt="kiosk")


def verify_kiosk_token(token: str) -> int | None:
    """Returns the admin id that started the kiosk, or None."""
    try:
        data = _get_signer().loads(token, salt="kiosk", max_age=KIOSK_TTL_SECONDS)
        return data["kiosk_by"]
    except (BadSignature, Exception):
        return None


# Short-lived token proving a kiosk-return client just passed the identifier+PIN
# check. Deliberately separate from sb_session (30 days) — this must not survive
# past the single update the client is doing right now.
CLIENT_TTL_SECONDS = 15 * 60


def make_client_token(user_id: int) -> str:
    return _get_signer().dumps({"uid": user_id}, salt="client")


def verify_client_token(token: str) -> int | None:
    try:
        data = _get_signer().loads(token, salt="client", max_age=CLIENT_TTL_SECONDS)
        return data["uid"]
    except (BadSignature, Exception):
        return None


# Staff (therapist) unlock for the kiosk "show me the last submission" screen.
STAFF_TTL_SECONDS = 15 * 60


def make_staff_token() -> str:
    return _get_signer().dumps({"staff": True}, salt="staff")


def verify_staff_token(token: str) -> bool:
    try:
        data = _get_signer().loads(token, salt="staff", max_age=STAFF_TTL_SECONDS)
        return bool(data.get("staff"))
    except (BadSignature, Exception):
        return False


# Records which visit a kiosk device just submitted, so the staff preview can
# only ever show "the visit that just happened here" — not an arbitrary id.
# Longer-lived than the staff unlock itself: the therapist may not check the
# tablet for a while after the client leaves.
LAST_VISIT_TTL_SECONDS = 3600


def make_last_visit_token(visit_id: int) -> str:
    return _get_signer().dumps({"visit_id": visit_id}, salt="last_visit")


def verify_last_visit_token(token: str) -> int | None:
    try:
        data = _get_signer().loads(token, salt="last_visit", max_age=LAST_VISIT_TTL_SECONDS)
        return data["visit_id"]
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


def send_lockout_alert(context: str, key: str) -> None:
    """Best-effort notification when a PIN lockout triggers — a signal of a
    brute-force attempt, not routine traffic (fires once per lockout, not
    per failed attempt, see identity.record_failure). Never raises: a broken
    mailer must not affect the lockout itself, which already did its job."""
    if not config.ALERT_EMAIL or not config.GMAIL_USER or not config.GMAIL_APP_PASSWORD:
        log.warning("Lockout on %s (%s) — ALERT_EMAIL not configured, not sending", context, key)
        return
    msg = MIMEText(
        f"PIN lockout triggered on {context}.\n\n"
        f"Source: {key}\n"
        f"5 wrong PIN attempts in a row — locked out for 15 minutes.\n\n"
        f"If this wasn't you or your staff, someone may be trying to guess "
        f"the PIN from the internet.",
        "plain",
        "utf-8",
    )
    msg["Subject"] = f"Sense Balance — PIN lockout on {context}"
    msg["From"] = config.GMAIL_USER
    msg["To"] = config.ALERT_EMAIL
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
            s.sendmail(config.GMAIL_USER, [config.ALERT_EMAIL], msg.as_string())
        log.info("Lockout alert sent for %s", context)
    except Exception:
        log.exception("Failed to send lockout alert for %s", context)
