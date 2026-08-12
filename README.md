# Sense Balance

Client intake app for a Thai massage practice. Clients fill in a bilingual
health/preferences questionnaire (Czech · English) once; the therapist sees it
translated to Thai before the session.

FastAPI + Jinja2 + SQLite, served as server-rendered HTML. No build step, no JS
framework.

## Features

- **Client intake form** — personal details, health status, lifestyle, massage
  preferences, body map (focus/avoid zones), consent with drawn signature.
- **Kiosk mode**, two flows on a shared tablet:
  - **First visit** — full questionnaire, ending with the client choosing a
    4-digit PIN (used to pull up their card next time).
  - **Return visit** — client enters email/phone + PIN, sees a read-only recap
    of what's on file, opens only the sections they want to change (tiles:
    health, lifestyle, preferences, body map), and always re-signs — even if
    nothing changed.
  - Every submission — first or return — is appended as an immutable row in
    the `visit` table, never overwritten. `profile` holds the latest state for
    prefilling the form; `visit` is the audit trail proving what was signed
    and when.
  - A therapist-only "for the therapist" screen (`/kiosk/last`, gated by
    `STAFF_PIN`) shows the last submission made on that device, in CZ/EN/TH.
- **Two login paths** (non-kiosk account holders) — email magic link (Gmail
  SMTP) or Google OAuth.
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
| `GET /` | Landing / login |
| `GET /health` | Health check (used by the deploy workflow) |
| `POST /auth/magic-request`, `GET /auth/magic-verify` | Magic-link login |
| `GET /auth/google`, `/auth/google/callback` | Google OAuth login |
| `GET/POST /profile` | Client questionnaire (logged-in account) |
| `POST /profile/name`, `/profile/delete` | Rename, delete account (GDPR erasure) |
| `POST /admin/kiosk/start` | Start a kiosk session on this device |
| `GET /kiosk` | Kiosk welcome screen: first visit / returning |
| `GET/POST /kiosk/new` | First-visit form (+ PIN selection) |
| `GET/POST /kiosk/return` | Return-visit identity check (identifier + PIN) |
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
  routes/kiosk.py kiosk welcome/new/return/update/done/staff-preview routes
  db.py           SQLite schema, migrations, queries
  auth.py         session/kiosk/client/staff tokens, Gmail send
  i18n.py         all UI strings and domain labels (cs/en/th)
  translate.py    Ollama translation call
  config.py       env-var configuration
  templates/      Jinja2 templates
  static/         intake.js (shared form logic), PWA manifest + service worker
design/           reference images for the form design
tests/            pytest suite (kiosk return flow, visit history, staff view)
```

Data lives in SQLite at `$DATA_DIR/app.db`: `user`, `profile` (latest state,
one row per client), `visit` (append-only submission history — never updated
or deleted except via cascade on account deletion), `magic_token`. Columns
added after the initial release are applied at startup by `_migrate()` in
`db.py` — there is no migration tool.

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

The first admin has to be promoted by hand:

```bash
sqlite3 data/app.db "UPDATE user SET is_admin = 1 WHERE email = 'you@example.com';"
```

## Testing

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Covers the security-sensitive kiosk return flow: PIN verification, lockout,
that a forged request body can't reassign a submission to another account,
that consent+signature are enforced server-side, that visits are append-only,
and that the staff preview stays PIN-gated. The rest of the app has no test
coverage — this is a deliberate, narrow addition, not a general test suite.

## Deployment

`STAFF_PIN` and a real `SECRET_KEY` must be set in the server's
`docker-compose.yml` env (that file lives on the server, not in this repo) —
without them the therapist preview never unlocks and the app won't start.


Push to `main` triggers `.github/workflows/deploy.yml` on a self-hosted runner:
rsync into `/srv/docker/apps/sensebalance`, rebuild the compose service,
health-check `:8093/health`, then mirror the repo to Forgejo as backup (skipped
if `FORGEJO_TOKEN` is unset).
