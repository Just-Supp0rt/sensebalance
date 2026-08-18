"""Kiosk (shared salon tablet) flow: welcome screen, first-visit form,
PIN-gated return flow, and a staff-PIN preview of the last submission.

Split out of app/main.py because this is where nearly all of the new
complexity for the tablet kiosk lives — see app/deps.py for the helpers
shared with the rest of the app.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import secrets

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import auth, config, identity
from app.deps import _current_user_id, _profile_to_dict, _require_admin, _resp, get_db
from app.i18n import BODY_ZONES, HEALTH_FLAGS, PROBLEM_TAGS
from app.i18n import bi as i18n_bi
from app.i18n import t as i18n_t
from app.translate import translate

log = logging.getLogger(__name__)
router = APIRouter()

_VALID_ZONE_IDS = {z["id"] for z in BODY_ZONES}
_VALID_PROBLEM_IDS = {p["id"] for p in PROBLEM_TAGS}
_VALID_HEALTH_FLAG_IDS = {f["id"] for f in HEALTH_FLAGS}
_VALID_PRESSURE = {"very_light", "light", "medium", "strong"}
_VALID_YN = {"yes", "no", ""}
_VALID_BP = {"high", "low", "normal", ""}
_VALID_SECTIONS = {"health", "lifestyle", "preferences", "bodymap"}
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

MAX_SIGNATURE_BYTES = 200_000


def _err(msg_key: str, status: int = 400) -> JSONResponse:
    cs, en = i18n_bi(msg_key)
    return JSONResponse(
        {"error": msg_key, "error_message": f"{cs} / {en}"}, status_code=status
    )


def _valid_email(s: str) -> bool:
    return bool(_EMAIL_RE.fullmatch(s or ""))


def _valid_signature(png: str) -> bool:
    if not png or not png.startswith("data:image/png;base64,"):
        return False
    try:
        raw = base64.b64decode(png.split(",", 1)[1], validate=True)
    except Exception:
        return False
    return 0 < len(raw) <= MAX_SIGNATURE_BYTES


def _choice(value, allowed: set[str]) -> str:
    return value if value in allowed else ""


def _ids(values, allowed: set[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    return [v for v in values if v in allowed]


def clean_intake_data(raw: dict) -> dict:
    """Sanitize a submitted intake payload against known enums — the client-side
    JS already restricts choices to valid buttons, but that's not a security
    boundary; a forged POST can send anything."""
    return {
        "name": (raw.get("name") or "").strip()[:200],
        "phone": identity.normalize_phone(raw.get("phone") or ""),
        "email": (raw.get("email") or "").strip().lower()[:200],
        "has_health_problems": _choice(raw.get("has_health_problems"), _VALID_YN),
        "health_problems": (raw.get("health_problems") or "").strip()[:1000],
        "pregnancy": _choice(raw.get("pregnancy"), _VALID_YN),
        "blood_pressure": _choice(raw.get("blood_pressure"), _VALID_BP),
        "exercise": _choice(raw.get("exercise"), _VALID_YN),
        "exercise_detail": (raw.get("exercise_detail") or "").strip()[:500],
        "recent_surgery": _choice(raw.get("recent_surgery"), _VALID_YN),
        "surgery_detail": (raw.get("surgery_detail") or "").strip()[:500],
        "pressure": raw.get("pressure") if raw.get("pressure") in _VALID_PRESSURE else "medium",
        "problem_tags": _ids(raw.get("problem_tags"), _VALID_PROBLEM_IDS),
        "health_flags": _ids(raw.get("health_flags"), _VALID_HEALTH_FLAG_IDS),
        "focus_zones": _ids(raw.get("focus_zones"), _VALID_ZONE_IDS),
        "avoid_zones": _ids(raw.get("avoid_zones"), _VALID_ZONE_IDS),
        "oil_allergies": (raw.get("oil_allergies") or "").strip()[:500],
        "note_original": (raw.get("note_original") or "").strip()[:2000],
        "consent": bool(raw.get("consent")),
        "signature_png": raw.get("signature_png") or "",
    }


def _translate_and_store(user_id: int, visit_id: int, note: str) -> None:
    """Runs as a FastAPI BackgroundTask (Starlette executes sync callables in a
    worker thread automatically) — the client is already looking at the
    'thank you' screen by the time this runs, so a slow or unreachable Ollama
    never blocks the kiosk."""
    if not note.strip():
        return
    db = get_db()
    for lang, field in (("th", "note_th"), ("en", "note_en")):
        text = translate(note, lang)
        if text:
            db.update_profile_translation(user_id, field, text)
            db.update_visit_translation(visit_id, field, text)


# --- cookie helpers ---

def _kiosk_ok(request: Request) -> bool:
    token = request.cookies.get("sb_kiosk")
    return bool(token and auth.verify_kiosk_token(token))


def _client_uid(request: Request) -> int | None:
    token = request.cookies.get("sb_client")
    if not token:
        return None
    return auth.verify_client_token(token)


# --- admin: start kiosk mode on this device ---

_KIOSK_START_TARGETS = {"/kiosk", "/kiosk/new"}


@router.post("/admin/kiosk/start")
async def kiosk_start(request: Request, target: str = Form("/kiosk")):
    redir = _require_admin(request)
    if redir:
        return redir
    if target not in _KIOSK_START_TARGETS:
        target = "/kiosk"

    uid = _current_user_id(request)
    resp = RedirectResponse(target, status_code=302)
    resp.set_cookie(
        "sb_kiosk",
        auth.make_kiosk_token(uid),
        max_age=auth.KIOSK_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    # log the admin out on this device so the client can't reach /admin
    resp.delete_cookie("sb_session")
    return resp


# --- search screen (staff-driven, replaces the old client PIN self-service) ---
# Aom looks the client up by phone/email before handing them the tablet. The
# client never types a PIN — but the search itself is gated on STAFF_PIN
# (same secret as /kiosk/last), entered fresh on every lookup. Without this,
# anyone holding the tablet in kiosk mode could search and read any other
# client's health data by guessing a phone number — the exact leak the old
# client-PIN design existed to prevent, just moved to a different door.

def _search_ctx(**extra) -> dict:
    # Kiosk mode has no logged-in session (sb_session is dropped by
    # /admin/kiosk/start), so the request-locale `t()` falls back to the
    # browser's Accept-Language — not necessarily Thai. These screens are
    # operated by Aom, so pin the primary-language strings to Thai explicitly.
    return {"kiosk": True, "t_loc": lambda k: i18n_t("th", k), **extra}


@router.get("/kiosk", response_class=HTMLResponse)
async def kiosk_search_form(request: Request):
    if not _kiosk_ok(request):
        return RedirectResponse("/", status_code=302)
    resp = _resp(request, "kiosk_search.html", _search_ctx(no_match=False, error=None))
    resp.delete_cookie("sb_client")
    return resp


@router.post("/kiosk/search", response_class=HTMLResponse)
async def kiosk_search_submit(
    request: Request, identifier: str = Form(...), pin: str = Form(...)
):
    if not _kiosk_ok(request):
        return RedirectResponse("/", status_code=302)

    ip = identity.client_ip(request)
    lock_key = f"search_pin:{ip}"

    if identity.is_locked(lock_key):
        cs, en = i18n_bi("return_locked")
        return _resp(
            request,
            "kiosk_search.html",
            _search_ctx(no_match=False, error=f"{cs} / {en}"),
            status=429,
        )
    if not identity.check_rate_limit(f"search_rate:{ip}"):
        cs, en = i18n_bi("return_locked")
        return _resp(
            request,
            "kiosk_search.html",
            _search_ctx(no_match=False, error=f"{cs} / {en}"),
            status=429,
        )

    ok = bool(config.STAFF_PIN) and secrets.compare_digest(pin, config.STAFF_PIN)
    if not ok:
        if identity.record_failure(lock_key):
            auth.send_lockout_alert("kiosk search", ip)
        cs, en = i18n_bi("staff_pin_wrong")
        return _resp(
            request,
            "kiosk_search.html",
            _search_ctx(no_match=False, error=f"{cs} / {en}"),
            status=401,
        )
    identity.record_success(lock_key)

    norm_id = identity.normalize_identifier(identifier)
    db = get_db()
    user = db.find_user_by_identifier(norm_id) if norm_id else None

    if not user:
        return _resp(
            request,
            "kiosk_search.html",
            _search_ctx(no_match=True, error=None, searched=identifier.strip()),
        )

    last_visit = db.latest_visit(user["id"])
    return _resp(
        request,
        "kiosk_search_confirm.html",
        _search_ctx(
            match=user,
            last_visit_at=last_visit["created_at"] if last_visit else None,
        ),
    )


@router.post("/kiosk/search/confirm")
async def kiosk_search_confirm(request: Request, user_id: int = Form(...)):
    if not _kiosk_ok(request):
        return RedirectResponse("/", status_code=302)
    db = get_db()
    user = db.get_user_by_id(user_id)
    if not user:
        return RedirectResponse("/kiosk", status_code=302)

    resp = RedirectResponse("/kiosk/update", status_code=302)
    resp.set_cookie(
        "sb_client",
        auth.make_client_token(user["id"]),
        max_age=auth.CLIENT_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return resp


# --- first visit ---

@router.get("/kiosk/new", response_class=HTMLResponse)
async def kiosk_new_form(request: Request, identifier: str = ""):
    if not _kiosk_ok(request):
        return RedirectResponse("/", status_code=302)
    profile = _profile_to_dict(None)
    # Aom already typed this on the search screen and it matched no one —
    # don't make her (or the client) type it a second time on this form.
    identifier = identifier.strip()
    if "@" in identifier:
        profile["email"] = identifier
    elif identifier:
        profile["phone"] = identifier
    return _resp(
        request,
        "kiosk_new.html",
        {"profile": profile, "kiosk": True},
    )


@router.post("/kiosk/new")
@router.post("/kiosk")  # legacy alias for any in-flight kiosk tab
async def kiosk_new_submit(
    request: Request, background_tasks: BackgroundTasks, body: str = Form(...)
):
    if not _kiosk_ok(request):
        return _err("return_locked", status=403)
    try:
        raw = json.loads(body)
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)

    clean = clean_intake_data(raw)
    if not clean["name"] or not _valid_email(clean["email"]):
        return _err("identifier_label")
    if not clean["phone"]:
        return _err("phone_required")
    if not clean["consent"] or not _valid_signature(clean["signature_png"]):
        return _err("consent_required")

    db = get_db()
    user = db.ensure_kiosk_user(clean["email"], clean["phone"])
    db.set_user_name(user["id"], clean["name"])

    clean["note_lang"] = "cs"
    clean["note_th"] = ""
    clean["note_en"] = ""
    visit_id = db.record_submission(user["id"], clean, kind="first", changed_sections=[])
    background_tasks.add_task(_translate_and_store, user["id"], visit_id, clean["note_original"])

    resp = JSONResponse({"ok": True, "redirect": "/kiosk/done"})
    resp.set_cookie(
        "sb_last_visit",
        auth.make_last_visit_token(visit_id),
        max_age=auth.LAST_VISIT_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return resp


# --- return visit: recap + targeted update ---

@router.get("/kiosk/update", response_class=HTMLResponse)
async def kiosk_update_form(request: Request):
    if not _kiosk_ok(request):
        return RedirectResponse("/", status_code=302)
    uid = _client_uid(request)
    if not uid:
        return RedirectResponse("/kiosk", status_code=302)
    db = get_db()
    user = db.get_user_by_id(uid)
    if not user:
        return RedirectResponse("/kiosk", status_code=302)
    profile = db.get_profile(uid)
    profile_data = _profile_to_dict(profile)
    last_visit = db.latest_visit(uid)
    return _resp(
        request,
        "kiosk_update.html",
        {
            "profile": profile_data,
            "kiosk": True,
            "client_name": user["name"],
            "client_email": user["email"],
            "last_visit_at": last_visit["created_at"] if last_visit else None,
        },
    )


@router.post("/kiosk/update")
async def kiosk_update_submit(
    request: Request, background_tasks: BackgroundTasks, body: str = Form(...)
):
    if not _kiosk_ok(request):
        return _err("return_locked", status=403)
    uid = _client_uid(request)
    if not uid:
        return _err("return_error", status=403)

    try:
        raw = json.loads(body)
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)

    db = get_db()
    user = db.get_user_by_id(uid)
    if not user:
        return _err("return_error", status=403)

    clean = clean_intake_data(raw)
    # Identity is fixed by the sb_client cookie, never by whatever the posted
    # JSON happens to contain — otherwise a forged body could reassign this
    # submission to a different account.
    clean["email"] = user["email"]
    clean["name"] = clean["name"] or user["name"]
    if not clean["consent"] or not _valid_signature(clean["signature_png"]):
        return _err("consent_required")

    changed_sections = [s for s in (raw.get("changed_sections") or []) if s in _VALID_SECTIONS]

    previous = db.get_profile(uid)
    prev_note = previous["note_original"] if previous else ""
    if clean["note_original"] != prev_note:
        clean["note_th"] = ""
        clean["note_en"] = ""
        needs_translation = bool(clean["note_original"])
    else:
        clean["note_th"] = previous["note_th"] if previous else ""
        clean["note_en"] = previous["note_en"] if previous else ""
        needs_translation = False
    clean["note_lang"] = "cs"

    visit_id = db.record_submission(uid, clean, kind="return", changed_sections=changed_sections)
    if needs_translation:
        background_tasks.add_task(_translate_and_store, uid, visit_id, clean["note_original"])

    resp = JSONResponse({"ok": True, "redirect": "/kiosk/done"})
    resp.set_cookie(
        "sb_last_visit",
        auth.make_last_visit_token(visit_id),
        max_age=auth.LAST_VISIT_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    resp.delete_cookie("sb_client")
    return resp


# --- thank you screen ---

@router.get("/kiosk/done", response_class=HTMLResponse)
async def kiosk_done(request: Request):
    if not _kiosk_ok(request):
        return RedirectResponse("/", status_code=302)
    resp = _resp(request, "kiosk_done.html", {"kiosk": True})
    resp.delete_cookie("sb_client")
    return resp


# --- staff (therapist) preview of the last submission on this device ---

def _staff_ok(request: Request) -> bool:
    token = request.cookies.get("sb_staff")
    return bool(token and auth.verify_staff_token(token))


def _staff_lang(request: Request) -> str:
    lang = request.query_params.get("lang", "th")
    return lang if lang in ("cs", "en", "th") else "th"


def _staff_ctx(request: Request, **extra) -> dict:
    lang = _staff_lang(request)
    return {"kiosk": True, "lang": lang, "t_loc": lambda k: i18n_t(lang, k), **extra}


@router.get("/kiosk/last", response_class=HTMLResponse)
async def kiosk_last(request: Request):
    if not _kiosk_ok(request):
        return RedirectResponse("/", status_code=302)
    if not _staff_ok(request):
        return _resp(request, "kiosk_staff.html", _staff_ctx(request, unlocked=False, error=None))

    visit_id = auth.verify_last_visit_token(request.cookies.get("sb_last_visit", ""))
    db = get_db()
    visit = db.get_visit(visit_id) if visit_id else None
    if not visit:
        return _resp(request, "kiosk_staff.html", _staff_ctx(request, unlocked=True, visit=None))

    client = db.get_user_by_id(visit["user_id"])
    snapshot = json.loads(visit["snapshot"])
    return _resp(
        request,
        "kiosk_staff.html",
        _staff_ctx(request, unlocked=True, visit=visit, snapshot=snapshot, client=client),
    )


@router.post("/kiosk/last")
async def kiosk_last_unlock(request: Request, pin: str = Form(...)):
    if not _kiosk_ok(request):
        return RedirectResponse("/", status_code=302)

    ip = identity.client_ip(request)
    lock_key = f"last_pin:{ip}"

    if identity.is_locked(lock_key):
        cs, en = i18n_bi("return_locked")
        return _resp(
            request,
            "kiosk_staff.html",
            _staff_ctx(request, unlocked=False, error=f"{cs} / {en}"),
            status=429,
        )

    ok = bool(config.STAFF_PIN) and secrets.compare_digest(pin, config.STAFF_PIN)
    if not ok:
        if identity.record_failure(lock_key):
            auth.send_lockout_alert("kiosk/last preview", ip)
        cs, en = i18n_bi("staff_pin_wrong")
        return _resp(
            request,
            "kiosk_staff.html",
            _staff_ctx(request, unlocked=False, error=f"{cs} / {en}"),
            status=401,
        )

    identity.record_success(lock_key)
    resp = RedirectResponse("/kiosk/last", status_code=302)
    resp.set_cookie(
        "sb_staff",
        auth.make_staff_token(),
        max_age=auth.STAFF_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return resp
