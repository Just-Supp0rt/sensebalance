from __future__ import annotations

import json

from tests.conftest import new_visit_payload


def _submit_new(client):
    return client.post("/kiosk/new", data={"body": json.dumps(new_visit_payload())})


def test_wrong_pin_and_unknown_user_return_identical_response(admin_client):
    r = _submit_new(admin_client)
    assert r.status_code == 200

    wrong = admin_client.post(
        "/kiosk/return", data={"identifier": "david@example.com", "pin": "0000"}
    )
    unknown = admin_client.post(
        "/kiosk/return", data={"identifier": "nobody@example.com", "pin": "0000"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.text == unknown.text


def test_sixth_attempt_is_locked_out(admin_client):
    _submit_new(admin_client)
    for _ in range(5):
        r = admin_client.post(
            "/kiosk/return", data={"identifier": "david@example.com", "pin": "0000"}
        )
        assert r.status_code == 401
    locked = admin_client.post(
        "/kiosk/return", data={"identifier": "david@example.com", "pin": "1234"}
    )
    assert locked.status_code == 429


def test_correct_pin_grants_client_cookie_and_redirects(admin_client):
    _submit_new(admin_client)
    r = admin_client.post(
        "/kiosk/return",
        data={"identifier": "david@example.com", "pin": "1234"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/kiosk/update"
    assert "sb_client" in admin_client.cookies


def test_update_requires_client_cookie(admin_client):
    r = admin_client.get("/kiosk/update", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/kiosk"


def test_update_ignores_forged_identity_in_body(admin_client, db):
    _submit_new(admin_client)
    admin_client.post(
        "/kiosk/return", data={"identifier": "david@example.com", "pin": "1234"}
    )
    other = db.ensure_kiosk_user("someone-else@example.com", "")

    payload = new_visit_payload(email="someone-else@example.com", name="Someone Else")
    r = admin_client.post("/kiosk/update", data={"body": json.dumps(payload)})
    assert r.status_code == 200

    david = db.get_user_by_email("david@example.com")
    victim_visits = db.list_visits(other["id"])
    assert victim_visits == []
    assert db.list_visits(david["id"])


def test_submission_without_consent_or_signature_is_rejected(admin_client):
    payload = new_visit_payload(consent=False)
    r = admin_client.post("/kiosk/new", data={"body": json.dumps(payload)})
    assert r.status_code == 400

    payload2 = new_visit_payload(signature_png="")
    r2 = admin_client.post("/kiosk/new", data={"body": json.dumps(payload2)})
    assert r2.status_code == 400
