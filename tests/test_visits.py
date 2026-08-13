from __future__ import annotations

import json

from tests.conftest import new_visit_payload, search_and_confirm


def test_two_submissions_create_two_immutable_visit_rows(admin_client, db):
    admin_client.post("/kiosk/new", data={"body": json.dumps(new_visit_payload())})
    search_and_confirm(admin_client, "david@example.com")
    update_payload = new_visit_payload(
        has_health_problems="yes",
        health_problems="nova bolest zad",
        changed_sections=["health"],
    )
    r = admin_client.post("/kiosk/update", data={"body": json.dumps(update_payload)})
    assert r.status_code == 200

    user = db.get_user_by_email("david@example.com")
    visits = db.list_visits(user["id"])
    assert len(visits) == 2

    kinds = {v["kind"] for v in visits}
    assert kinds == {"first", "return"}

    first = next(v for v in visits if v["kind"] == "first")
    first_snapshot = json.loads(first["snapshot"])
    assert first_snapshot["has_health_problems"] == "no"


def test_no_change_submission_still_records_full_snapshot(admin_client, db):
    admin_client.post("/kiosk/new", data={"body": json.dumps(new_visit_payload())})
    search_and_confirm(admin_client, "david@example.com")
    r = admin_client.post(
        "/kiosk/update", data={"body": json.dumps(new_visit_payload(changed_sections=[]))}
    )
    assert r.status_code == 200

    user = db.get_user_by_email("david@example.com")
    visits = db.list_visits(user["id"])
    return_visit = next(v for v in visits if v["kind"] == "return")
    snapshot = json.loads(return_visit["snapshot"])
    assert snapshot["problem_tags"] == ["back_pain"]
    assert snapshot["signature_png"]


def test_delete_user_cascades_to_visits(admin_client, db):
    admin_client.post("/kiosk/new", data={"body": json.dumps(new_visit_payload())})
    user = db.get_user_by_email("david@example.com")
    assert db.list_visits(user["id"])

    db.delete_user(user["id"])
    remaining = db._conn.execute(
        "SELECT COUNT(*) c FROM visit WHERE user_id=?", (user["id"],)
    ).fetchone()["c"]
    assert remaining == 0
