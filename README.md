# Sense Balance

Client intake app for a Thai massage practice. Clients fill in a bilingual
health/preferences questionnaire (Czech · English) once; the therapist sees it
translated to Thai before the session.

FastAPI + Jinja2 + SQLite, served as server-rendered HTML. No build step, no JS
framework.

## Features

- **Client intake form** — personal details, health status, lifestyle, massage
  preferences, body map (focus/avoid zones), consent with drawn signature.
- **Kiosk mode** — the therapist starts a kiosk session on a tablet from
  `/admin`; the client fills the form on the spot without an account, and the
  admin is logged out on that device so the client cannot reach `/admin`.
- **Two login paths** — email magic link (sent over Gmail SMTP) or Google OAuth.
- **Thai translation** — free-text notes are translated on save via a local
  Ollama instance and cached on the profile row. Best-effort: a failure leaves
  the Thai field empty, it never blocks the save.
- **Trilingual UI** — `cs` / `en` / `th`, resolved from the user's stored locale
  or `Accept-Language`.
- **Admin view** — client list with search, plus a per-client detail page.

## Routes

| Route | Purpose |
|---|---|
| `GET /` | Landing / login |
| `GET /health` | Health check (used by the deploy workflow) |
| `POST /auth/magic-request`, `GET /auth/magic-verify` | Magic-link login |
| `GET /auth/google`, `/auth/google/callback` | Google OAuth login |
| `GET/POST /profile` | Client questionnaire |
| `POST /profile/name`, `/profile/delete` | Rename, delete account |
| `POST /admin/kiosk/start` | Start a kiosk session on this device |
| `GET/POST /kiosk`, `GET /kiosk/done` | Kiosk intake flow |
| `GET /admin`, `/admin/client/{id}` | Therapist views |

## Layout

```
app/
  main.py       routes, request/response plumbing
  db.py         SQLite schema, migrations, queries
  auth.py       session + magic-link + kiosk tokens, Gmail send
  i18n.py       all UI strings and domain labels (cs/en/th)
  translate.py  Ollama translation call
  config.py     env-var configuration
  templates/    Jinja2 templates
  static/       PWA manifest + service worker
design/         reference images for the form design
```

Data lives in SQLite at `$DATA_DIR/app.db` (three tables: `user`, `profile`,
`magic_token`). Columns added after the initial release are applied at startup
by `_migrate()` in `db.py` — there is no migration tool.

## Configuration

All via environment variables (see `app/config.py`):

| Variable | Default | Notes |
|---|---|---|
| `DATA_DIR` | `/data` | SQLite location |
| `SECRET_KEY` | `dev-secret-change-in-prod` | **Must** be set in production — signs session and kiosk tokens |
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

## Deployment

Push to `main` triggers `.github/workflows/deploy.yml` on a self-hosted runner:
rsync into `/srv/docker/apps/sensebalance`, rebuild the compose service,
health-check `:8093/health`, then mirror the repo to Forgejo as backup (skipped
if `FORGEJO_TOKEN` is unset).
