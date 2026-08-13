from __future__ import annotations

import json
import logging
from pathlib import Path

from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import auth, config, identity
from app.deps import (
    _current_user_id,
    _profile_to_dict,
    _require_admin,
    _resp,
    get_db,
)
from app.i18n import bi as i18n_bi
from app.i18n import t as i18n_t
from app.routes.kiosk import router as kiosk_router
from app.translate import translate

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

if config.SECRET_KEY == "dev-secret-change-in-prod" and not config.DEV:
    raise RuntimeError(
        "SECRET_KEY is at its insecure default. The kiosk return flow, staff "
        "preview, and login sessions are all signed with this key — anyone who "
        "knows the default can forge a cookie and read another client's health "
        "data. Set SECRET_KEY in the environment, or set DEV=1 for local dev."
    )

app = FastAPI(title="Sense Balance")
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)
app.include_router(kiosk_router)


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


# --- staff login (name + PIN) ---
# Primary login path for Aom/Kuba, shown on login.html. Magic-link and Google
# OAuth below stay wired up but are no longer linked from the UI — cheaper to
# leave dormant than to rip out hours before a demo.

@app.post("/auth/staff-login")
async def staff_login(request: Request, name: str = Form(...), pin: str = Form(...)):
    key = f"staff-login:{name.strip().lower()}"
    if identity.is_locked(key):
        cs, en = i18n_bi("return_locked")
        return _resp(
            request, "login.html", {"error": f"{cs} / {en}", "sent": False}, status=429
        )

    db = get_db()
    user = db.get_admin_by_name(name.strip())
    ok = identity.verify_pin_hash(
        pin, user["pin_hash"] if user else "", user["pin_salt"] if user else ""
    )
    if not ok:
        identity.record_failure(key)
        cs, en = i18n_bi("return_error")
        return _resp(
            request, "login.html", {"error": f"{cs} / {en}", "sent": False}, status=401
        )

    identity.record_success(key)
    return _login_response(user["id"])


# --- magic link auth (dormant — not linked from the UI, see above) ---

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
        note_th = translate(note, "th")

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


# --- admin (Veronika) ---

@app.get("/admin", response_class=HTMLResponse)
async def admin_clients(request: Request, q: str = ""):
    redir = _require_admin(request)
    if redir:
        return redir
    users = get_db().list_users()
    if q:
        q_lower = q.lower()
        users = [u for u in users if q_lower in (u["email"] or "").lower() or q_lower in (u["name"] or "").lower()]
    return _resp(
        request,
        "admin_list.html",
        {"clients": users, "q": q, "t_loc": lambda k: i18n_t("th", k)},
    )


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
    visits = db.list_visits(user_id)
    lang = request.query_params.get("lang", "th")
    if lang not in ("cs", "en", "th"):
        lang = "th"
    return _resp(
        request,
        "admin_detail.html",
        {
            "client": client,
            "profile": profile_data,
            "visits": visits,
            "lang": lang,
            "t_loc": lambda k: i18n_t(lang, k),
        },
    )


@app.get("/admin/client/{user_id}/visit/{visit_id}", response_class=HTMLResponse)
async def admin_visit_detail(request: Request, user_id: int, visit_id: int):
    redir = _require_admin(request)
    if redir:
        return redir
    db = get_db()
    client = db.get_user_by_id(user_id)
    visit = db.get_visit(visit_id)
    if not client or not visit or visit["user_id"] != user_id:
        return RedirectResponse("/admin", status_code=302)
    snapshot = json.loads(visit["snapshot"])
    lang = request.query_params.get("lang", "th")
    if lang not in ("cs", "en", "th"):
        lang = "th"
    return _resp(
        request,
        "admin_visit_detail.html",
        {
            "client": client,
            "visit": visit,
            "profile": snapshot,
            "lang": lang,
            "t_loc": lambda k: i18n_t(lang, k),
        },
    )
