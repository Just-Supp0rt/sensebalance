from __future__ import annotations

import os

os.environ.setdefault("DEV", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("STAFF_PIN", "9999")

import pytest
from fastapi.testclient import TestClient

import app.deps as deps_module
from app import auth, identity
from app.db import DB


@pytest.fixture(autouse=True)
def reset_pin_lockout():
    # identity._attempts is a module-level dict shared across the whole test
    # process — without resetting it, lockout state from one test's failed
    # PIN attempts bleeds into the next test's "correct PIN" case.
    identity._attempts.clear()
    yield
    identity._attempts.clear()


@pytest.fixture()
def db(tmp_path):
    database = DB(tmp_path / "test.db")
    deps_module._db = database
    yield database
    deps_module._db = None


@pytest.fixture()
def client(db):
    from app.main import app

    return TestClient(app)


@pytest.fixture()
def admin_client(client, db):
    db.ensure_email_user("admin@sensebalance.cz")
    row = db.get_user_by_email("admin@sensebalance.cz")
    db._conn.execute("UPDATE user SET is_admin=1 WHERE id=?", (row["id"],))
    db._conn.commit()
    client.cookies.set("sb_session", auth.make_session_token(row["id"]))
    client.post("/admin/kiosk/start")
    return client


VALID_SIGNATURE = "data:image/png;base64," + ("A" * 100)


def new_visit_payload(**overrides) -> dict:
    payload = {
        "name": "David Test",
        "phone": "777123456",
        "email": "david@example.com",
        "has_health_problems": "no",
        "health_problems": "",
        "pregnancy": "no",
        "blood_pressure": "normal",
        "exercise": "no",
        "exercise_detail": "",
        "recent_surgery": "no",
        "surgery_detail": "",
        "pressure": "medium",
        "problem_tags": ["back_pain"],
        "health_flags": [],
        "focus_zones": [],
        "avoid_zones": [],
        "oil_allergies": "",
        "note_original": "",
        "consent": True,
        "signature_png": VALID_SIGNATURE,
        "pin": "1234",
        "pin_confirm": "1234",
    }
    payload.update(overrides)
    return payload
