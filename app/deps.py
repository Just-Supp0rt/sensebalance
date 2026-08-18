"""Shared helpers used by both app/main.py and app/routes/kiosk.py.

Split out so the kiosk router doesn't have to import from main.py (which would
create a circular import once main.py registers that router).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import auth, config
from app.db import DB
from app.i18n import BODY_ZONES, HEALTH_FLAGS, PROBLEM_TAGS, bi, t, zone_label

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_db: DB | None = None


def get_db() -> DB:
    global _db
    if _db is None:
        _db = DB(config.DB_PATH)
    return _db


def _current_user_id(request: Request) -> int | None:
    token = request.cookies.get("sb_session")
    if not token:
        return None
    return auth.verify_session_token(token)


def _locale(request: Request) -> str:
    uid = _current_user_id(request)
    if uid:
        row = get_db().get_user_by_id(uid)
        if row:
            return row["locale"]
    accept = request.headers.get("accept-language", "cs")
    if "th" in accept:
        return "th"
    if "en" in accept:
        return "en"
    return "cs"


def _resp(request: Request, template: str, ctx: dict, status: int = 200):
    locale = _locale(request)
    uid = _current_user_id(request)
    user = get_db().get_user_by_id(uid) if uid else None
    ctx.update(
        {
            "request": request,
            "locale": locale,
            "t": lambda k: t(locale, k),
            "bi": bi,
            "user": user,
            "is_admin": bool(user and user["is_admin"]),
            "zone_label": zone_label,
            "BODY_ZONES": BODY_ZONES,
            "PROBLEM_TAGS": PROBLEM_TAGS,
            "HEALTH_FLAGS": HEALTH_FLAGS,
            "GOOGLE_CLIENT_ID": config.GOOGLE_CLIENT_ID,
        }
    )
    return templates.TemplateResponse(template, ctx, status_code=status)


def _require_admin(request: Request):
    uid = _current_user_id(request)
    if not uid:
        return RedirectResponse("/", status_code=302)
    user = get_db().get_user_by_id(uid)
    if not user or not user["is_admin"]:
        return RedirectResponse("/profile", status_code=302)
    return None


def _profile_to_dict(profile) -> dict:
    if not profile:
        return {
            "focus_zones": [],
            "avoid_zones": [],
            "pressure": "medium",
            "problem_tags": [],
            "health_flags": [],
            "oil_allergies": "",
            "note_original": "",
            "note_th": "",
            "note_en": "",
            "phone": "",
            "email": "",
            "has_health_problems": "",
            "health_problems": "",
            "pregnancy": "",
            "blood_pressure": "",
            "exercise": "",
            "exercise_detail": "",
            "recent_surgery": "",
            "surgery_detail": "",
            "consent_at": None,
            "signature_png": "",
            "updated_at": None,
        }
    return {
        "focus_zones": _json_list(profile["focus_zones"]),
        "avoid_zones": _json_list(profile["avoid_zones"]),
        "pressure": profile["pressure"],
        "problem_tags": _json_list(profile["problem_tags"]),
        "health_flags": _json_list(profile["health_flags"]),
        "oil_allergies": profile["oil_allergies"] or "",
        "note_original": profile["note_original"] or "",
        "note_th": profile["note_th"] or "",
        "note_en": profile["note_en"] or "",
        "phone": profile["phone"] or "",
        "has_health_problems": profile["has_health_problems"] or "",
        "health_problems": profile["health_problems"] or "",
        "pregnancy": profile["pregnancy"] or "",
        "blood_pressure": profile["blood_pressure"] or "",
        "exercise": profile["exercise"] or "",
        "exercise_detail": profile["exercise_detail"] or "",
        "recent_surgery": profile["recent_surgery"] or "",
        "surgery_detail": profile["surgery_detail"] or "",
        "consent_at": profile["consent_at"],
        "signature_png": profile["signature_png"] or "",
        "updated_at": profile["updated_at"],
    }


def _json_list(raw: str | None) -> list:
    import json

    return json.loads(raw or "[]")
