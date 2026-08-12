from __future__ import annotations

import json

from tests.conftest import new_visit_payload


def test_staff_last_requires_staff_pin(admin_client):
    admin_client.post("/kiosk/new", data={"body": json.dumps(new_visit_payload())})
    r = admin_client.get("/kiosk/last")
    assert r.status_code == 200
    assert "David Test" not in r.text


def test_wrong_staff_pin_does_not_unlock(admin_client):
    admin_client.post("/kiosk/new", data={"body": json.dumps(new_visit_payload())})
    r = admin_client.post("/kiosk/last", data={"pin": "0000"})
    assert r.status_code == 401
    r2 = admin_client.get("/kiosk/last")
    assert "David Test" not in r2.text


def test_correct_staff_pin_shows_only_last_visit_on_device(admin_client):
    admin_client.post("/kiosk/new", data={"body": json.dumps(new_visit_payload())})
    r = admin_client.post("/kiosk/last", data={"pin": "9999"}, follow_redirects=True)
    assert r.status_code == 200
    assert "David Test" in r.text


def test_staff_view_respects_lang_switch(admin_client):
    admin_client.post("/kiosk/new", data={"body": json.dumps(new_visit_payload())})
    admin_client.post("/kiosk/last", data={"pin": "9999"})
    en = admin_client.get("/kiosk/last?lang=en")
    cs = admin_client.get("/kiosk/last?lang=cs")
    assert "Back pain" in en.text
    assert "Bolesti zad" in cs.text
