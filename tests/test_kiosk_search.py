from __future__ import annotations

import json

from tests.conftest import new_visit_payload, search_and_confirm


def _submit_new(client, **overrides):
    return client.post("/kiosk/new", data={"body": json.dumps(new_visit_payload(**overrides))})


def test_search_with_no_match_offers_first_visit_cta(admin_client):
    r = admin_client.post("/kiosk/search", data={"identifier": "nobody@example.com"})
    assert r.status_code == 200
    assert "/kiosk/new" in r.text


def test_search_with_match_shows_confirm_screen(admin_client):
    _submit_new(admin_client)
    r = admin_client.post("/kiosk/search", data={"identifier": "david@example.com"})
    assert r.status_code == 200
    assert "David Test" in r.text
    assert 'name="user_id"' in r.text


def test_search_by_phone_also_finds_the_client(admin_client):
    _submit_new(admin_client)
    r = admin_client.post("/kiosk/search", data={"identifier": "777123456"})
    assert "David Test" in r.text


def test_confirm_sets_client_cookie_and_reaches_update(admin_client):
    _submit_new(admin_client)
    search_and_confirm(admin_client, "david@example.com")
    assert "sb_client" in admin_client.cookies

    r = admin_client.get("/kiosk/update")
    assert r.status_code == 200
    assert "David Test" in r.text


def test_confirm_rejects_unknown_user_id(admin_client):
    r = admin_client.post("/kiosk/search/confirm", data={"user_id": 99999}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/kiosk"
    assert "sb_client" not in admin_client.cookies


def test_first_visit_no_longer_asks_for_a_pin(admin_client):
    payload = new_visit_payload()
    assert "pin" not in payload

    r = _submit_new(admin_client)
    assert r.status_code == 200


def test_first_visit_requires_phone(admin_client):
    r = _submit_new(admin_client, phone="")
    assert r.status_code == 400
    assert r.json()["error"] == "phone_required"


def test_update_requires_client_cookie(admin_client):
    r = admin_client.get("/kiosk/update", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/kiosk"
