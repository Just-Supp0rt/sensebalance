# Sense Balance

Client intake app for a Thai massage practice. Clients fill in a bilingual
health/preferences questionnaire (Czech · English) once; the therapist sees it
translated to Thai before the session.

FastAPI + Jinja2 + SQLite, served as server-rendered HTML. No build step, no JS
framework.

## Features

- **Client intake form** — personal details, health status, lifestyle, massage
  preferences, body map (focus/avoid zones), consent with drawn signature.
- **Kiosk mode**, staff-driven identity — no client-facing PIN:
  - The therapist searches by phone or email (`/kiosk`) before handing the
    tablet to the client. No match → a "client's first visit" prompt starts
    the full first-visit form (name, phone, email all required). A match →
    a short "is this them?" confirmation (name + last visit date) before the
    client's card opens, then a read-only recap with tiles to open only the
    sections that changed (health, lifestyle, preferences, body map) — always
    re-signed, even if nothing changed.
  - Every submission — first or return — is appended as an immutable row in
    the `visit` table, never overwritten. `profile` holds the latest state for
    prefilling the form; `visit` is the audit trail proving what was signed
    and when.
  - A therapist-only "for the therapist" screen (`/kiosk/last`, gated by
    `STAFF_PIN`) shows the last submission made on that device, in CZ/EN/TH.
- **Staff login** — name + personal 4-digit PIN (`/auth/staff-login`), shown
  on the login screen. Email magic-link and Google OAuth routes still exist
  in `main.py` and work, but aren't linked from the UI.
- **Thai/English translation** — free-text notes are translated in the
  background (after the client's screen already shows "thank you") via a
  local Ollama instance, cached on both the profile row and the visit
  snapshot. Best-effort: a failure just leaves the field empty.
- **Trilingual UI** — `cs` / `en` / `th`. The client-facing intake form is
  always bilingual CZ+EN; admin/therapist views switch via `?lang=`.
- **Admin view** — client list with search, per-client detail with full visit
  history.

## Routes

| Route | Purpose |
|---|---|
| `GET /` | Landing / login (staff name + PIN) |
| `GET /health` | Health check (used by the deploy workflow) |
| `POST /auth/staff-login` | Admin login by name + personal PIN |
| `POST /auth/magic-request`, `GET /auth/magic-verify`, `GET /auth/google`, `/auth/google/callback` | Dormant — not linked from the UI, see Features |
| `GET/POST /profile` | Client questionnaire (logged-in account) |
| `POST /profile/name`, `/profile/delete` | Rename, delete account (GDPR erasure) |
| `POST /admin/kiosk/start` | Start a kiosk session on this device |
| `GET /kiosk` | Kiosk search screen (phone or email) |
| `POST /kiosk/search` | Look up a client; no match → first-visit CTA, match → confirm screen |
| `POST /kiosk/search/confirm` | Confirm the match, open that client's card |
| `GET/POST /kiosk/new` | First-visit form (name/phone/email all required) |
| `GET/POST /kiosk/update` | Return-visit recap + targeted update + signature |
| `GET /kiosk/done` | Thank-you screen (auto-resets to `/kiosk` after 60s) |
| `GET/POST /kiosk/last` | Staff-PIN-gated preview of the last submission |
| `GET /admin`, `/admin/client/{id}` | Therapist views |

## Layout

```
app/
  main.py         FastAPI app, auth/profile/admin routes
  deps.py         helpers shared between main.py and routes/kiosk.py
  identity.py     phone normalization, PIN hashing, lockout
  routes/kiosk.py kiosk search/new/update/done/staff-preview routes
  db.py           SQLite schema, migrations, queries
  auth.py         session/kiosk/client/staff tokens, Gmail send
  i18n.py         all UI strings and domain labels (cs/en/th)
  translate.py    Ollama translation call
  config.py       env-var configuration
  templates/      Jinja2 templates
  static/         intake.js (shared form logic), PWA manifest + service worker
design/           reference images for the form design
tests/            pytest suite (kiosk search flow, staff login, visit history, staff view)
```

Data lives in SQLite at `$DATA_DIR/app.db`: `user` (`pin_hash`/`pin_salt` are
now admin-only — the staff name+PIN login; clients no longer have a PIN),
`profile` (latest state, one row per client), `visit` (append-only submission
history — never updated or deleted except via cascade on account deletion),
`magic_token`. Columns added after the initial release are applied at startup
by `_migrate()` in `db.py` — there is no migration tool.

## Configuration

All via environment variables (see `app/config.py`):

| Variable | Default | Notes |
|---|---|---|
| `DATA_DIR` | `/data` | SQLite location |
| `SECRET_KEY` | `dev-secret-change-in-prod` | **Must** be set in production — signs session, kiosk, client and staff tokens. The app refuses to start on the default value unless `DEV=1` is set. |
| `STAFF_PIN` | empty | 4-digit PIN for the therapist's `/kiosk/last` preview. Unset ⇒ that screen never unlocks. |
| `DEV` | unset | Set to `1` to allow the insecure default `SECRET_KEY` locally |
| `BASE_URL` | `http://localhost:8093` | Used to build magic links |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD` | empty | Unset ⇒ magic links are logged, not sent |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | empty | Google OAuth |
| `OLLAMA_URL` | `http://192.168.1.159:11434` | Translation backend |
| `OLLAMA_MODEL` | `qwen3:8b` | |

## Running locally

```bash
pip install -r requirements.txt
DATA_DIR=./data SECRET_KEY=dev uvicorn app.main:app --reload --port 8093
```

Or with Docker:

```bash
docker build -t sensebalance .
docker run -p 8093:8093 -v $PWD/data:/data -e SECRET_KEY=dev sensebalance
```

The `python:3.12-slim` image has no `sqlite3` CLI, so admin setup goes through
Python's stdlib `sqlite3` module instead (works the same in `docker run`/
`docker compose exec`/local venv — swap the path for `data/app.db` locally).
The staff login (`/`, name + PIN) only works for accounts that already have
`is_admin=1` and a PIN, so create/update the account directly — no prior
login needed:

```bash
python3 -c "
import sqlite3
from datetime import datetime, timezone
from app.identity import hash_pin

email, name, pin = 'aom@example.com', 'Aom', '0209'
h, s = hash_pin(pin)
conn = sqlite3.connect('/data/app.db')
now = datetime.now(timezone.utc).isoformat()
cur = conn.execute(
    'UPDATE user SET is_admin=1, name=?, pin_hash=?, pin_salt=? WHERE email=?',
    (name, h, s, email),
)
if cur.rowcount == 0:
    conn.execute(
        'INSERT INTO user (email, name, provider, is_admin, pin_hash, pin_salt, created_at) '
        \"VALUES (?, ?, 'email', 1, ?, ?, ?)\",
        (email, name, h, s, now),
    )
conn.commit()
"
```

They can then log in at `/` with that `name` + PIN.

## Testing

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Covers the security-sensitive paths: staff name+PIN login (lockout, generic
error on wrong PIN/unknown name, admin-only), the search+confirm kiosk flow
(no match → first-visit CTA, confirm rejects an unknown user id), that a
forged request body can't reassign a submission to another account, that
consent+signature are enforced server-side, that visits are append-only, and
that the staff preview stays PIN-gated. The rest of the app has no test
coverage — this is a deliberate, narrow addition, not a general test suite.

## Deployment

`STAFF_PIN` and a real `SECRET_KEY` must be set in the server's
`docker-compose.yml` env (that file lives on the server, not in this repo) —
without them the therapist preview never unlocks and the app won't start.


Push to `main` triggers `.github/workflows/deploy.yml` on a self-hosted runner:
rsync into `/srv/docker/apps/sensebalance`, rebuild the compose service,
health-check `:8093/health`, then mirror the repo to Forgejo as backup (skipped
if `FORGEJO_TOKEN` is unset).
