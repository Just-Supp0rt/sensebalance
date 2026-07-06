from __future__ import annotations

import json
import logging
from pathlib import Path

from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import auth, config
from app.db import DB
from app.i18n import BODY_ZONES, HEALTH_FLAGS, PROBLEM_TAGS, bi, t, zone_label
from app.translate import translate_to_thai

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(title="Sense Balance")
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_db: DB | None = None


def get_db() -> DB:
    global _db
    if _db is None:
        _db = DB(config.DB_PATH)
    return _db


# Google OAuth
oauth = OAuth()
oauth.register(
    name="google",
    client_id=config.GOOGLE_CLIENT_ID,
    client_secret=config.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# --- helpers ---

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


def _login_response(user_id: int, redirect: str = "/profile") -> RedirectResponse:
    token = auth.make_session_token(user_id)
    resp = RedirectResponse(redirect, status_code=302)
    resp.set_cookie(
        "sb_session",
        token,
        max_age=config.SESSION_TTL_DAYS * 86400,
        httponly=True,
        samesite="lax",
    )
    return resp


# --- public routes ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    uid = _current_user_id(request)
    if uid:
        return RedirectResponse("/profile", status_code=302)
    return _resp(request, "login.html", {"sent": False})


@app.get("/health")
async def health():
    return {"ok": True}


# --- magic link auth ---

@app.post("/auth/magic-request")
async def magic_request(request: Request, email: str = Form(...)):
    email = email.strip().lower()
    db = get_db()
    token = auth.generate_magic_token()
    expiry = auth.magic_token_expiry()
    db.create_magic_token(token, email, expiry)
    link = f"{config.BASE_URL}/auth/magic-verify?token={token}"
    auth.send_magic_link(email, link)
    return _resp(request, "login.html", {"sent": True, "sent_email": email})


@app.get("/auth/magic-verify", response_class=HTMLResponse)
async def magic_verify(request: Request, token: str):
    db = get_db()
    email = db.consume_magic_token(token)
    if not email:
        return _resp(request, "login.html", {"error": "Odkaz vypršel nebo byl již použit."})
    user = db.ensure_email_user(email)
    return _login_response(user["id"])


# --- Google OAuth ---

@app.get("/auth/google")
async def google_login(request: Request):
    redirect_uri = f"{config.BASE_URL}/auth/google/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/google/callback")
async def google_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        log.exception("Google OAuth failed")
        return RedirectResponse("/?error=google", status_code=302)
    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email", "")
    name = userinfo.get("name", "")
    sub = userinfo.get("sub", "")
    if not email:
        return RedirectResponse("/?error=no_email", status_code=302)
    db = get_db()
    user = db.upsert_google_user(email, name, sub)
    return _login_response(user["id"])


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie("sb_session")
    return resp


# --- client profile ---

@app.get("/profile", response_class=HTMLResponse)
async def profile_get(request: Request):
    uid = _current_user_id(request)
    if not uid:
        return RedirectResponse("/", status_code=302)
    db = get_db()
    profile = db.get_profile(uid)
    profile_data = _profile_to_dict(profile)
    return _resp(request, "profile.html", {"profile": profile_data, "flash": None})


@app.post("/profile")
async def profile_post(request: Request, body: str = Form(...)):
    uid = _current_user_id(request)
    if not uid:
        return RedirectResponse("/", status_code=302)
    try:
        data = json.loads(body)
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)

    note = data.get("note_original", "").strip()
    note_th = ""
    if note:
        note_th = translate_to_thai(note)

    data["note_th"] = note_th
    data["note_lang"] = "cs"  # assume CS; could detect later
    if data.get("name", "").strip():
        get_db().set_user_name(uid, data["name"].strip())
    get_db().save_profile(uid, data)
    return JSONResponse({"ok": True})


@app.post("/profile/name")
async def set_name(request: Request, name: str = Form(...)):
    uid = _current_user_id(request)
    if not uid:
        return RedirectResponse("/", status_code=302)
    get_db().set_user_name(uid, name.strip())
    return RedirectResponse("/profile", status_code=302)


@app.post("/profile/delete")
async def delete_account(request: Request):
    uid = _current_user_id(request)
    if not uid:
        return RedirectResponse("/", status_code=302)
    get_db().delete_user(uid)
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie("sb_session")
    return resp


# --- kiosk (shared salon tablet) ---

def _kiosk_ok(request: Request) -> bool:
    token = request.cookies.get("sb_kiosk")
    return bool(token and auth.verify_kiosk_token(token))


@app.post("/admin/kiosk/start")
async def kiosk_start(request: Request):
    redir = _require_admin(request)
    if redir:
        return redir
    uid = _current_user_id(request)
    resp = RedirectResponse("/kiosk", status_code=302)
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


@app.get("/kiosk", response_class=HTMLResponse)
async def kiosk_form(request: Request):
    if not _kiosk_ok(request):
        return RedirectResponse("/", status_code=302)
    return _resp(
        request,
        "profile.html",
        {"profile": _profile_to_dict(None), "kiosk": True, "flash": None},
    )


@app.post("/kiosk")
async def kiosk_submit(request: Request, body: str = Form(...)):
    if not _kiosk_ok(request):
        return JSONResponse({"error": "kiosk expired"}, status_code=403)
    try:
        data = json.loads(body)
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)

    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()
    if not email or not name:
        return JSONResponse({"error": "missing name/email"}, status_code=400)
    if not data.get("consent") or not data.get("signature_png"):
        return JSONResponse({"error": "consent required"}, status_code=400)

    note = data.get("note_original", "").strip()
    data["note_th"] = translate_to_thai(note) if note else ""
    data["note_lang"] = "cs"

    db = get_db()
    user = db.ensure_email_user(email)
    db.set_user_name(user["id"], name)
    db.save_profile(user["id"], data)
    return JSONResponse({"ok": True, "redirect": "/kiosk/done"})


@app.get("/kiosk/done", response_class=HTMLResponse)
async def kiosk_done(request: Request):
    if not _kiosk_ok(request):
        return RedirectResponse("/", status_code=302)
    return _resp(request, "kiosk_done.html", {"kiosk": True})


# --- admin (Veronika) ---

def _require_admin(request: Request):
    uid = _current_user_id(request)
    if not uid:
        return RedirectResponse("/", status_code=302)
    user = get_db().get_user_by_id(uid)
    if not user or not user["is_admin"]:
        return RedirectResponse("/profile", status_code=302)
    return None


@app.get("/admin", response_class=HTMLResponse)
async def admin_clients(request: Request, q: str = ""):
    redir = _require_admin(request)
    if redir:
        return redir
    users = get_db().list_users()
    if q:
        q_lower = q.lower()
        users = [u for u in users if q_lower in (u["email"] or "").lower() or q_lower in (u["name"] or "").lower()]
    return _resp(request, "admin_list.html", {"clients": users, "q": q})


@app.get("/admin/client/{user_id}", response_class=HTMLResponse)
async def admin_client_detail(request: Request, user_id: int):
    redir = _require_admin(request)
    if redir:
        return redir
    db = get_db()
    client = db.get_user_by_id(user_id)
    if not client:
        return RedirectResponse("/admin", status_code=302)
    profile = db.get_profile(user_id)
    profile_data = _profile_to_dict(profile)
    return _resp(request, "admin_detail.html", {"client": client, "profile": profile_data})


# --- helper ---

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
            "phone": "",
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
        "focus_zones": json.loads(profile["focus_zones"] or "[]"),
        "avoid_zones": json.loads(profile["avoid_zones"] or "[]"),
        "pressure": profile["pressure"],
        "problem_tags": json.loads(profile["problem_tags"] or "[]"),
        "health_flags": json.loads(profile["health_flags"] or "[]"),
        "oil_allergies": profile["oil_allergies"] or "",
        "note_original": profile["note_original"] or "",
        "note_th": profile["note_th"] or "",
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
