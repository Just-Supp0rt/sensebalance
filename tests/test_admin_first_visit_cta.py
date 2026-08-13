from __future__ import annotations

from app import auth


def _login_admin(client, db):
    db.ensure_email_user("admin@sensebalance.cz")
    row = db.get_user_by_email("admin@sensebalance.cz")
    db._conn.execute("UPDATE user SET is_admin=1 WHERE id=?", (row["id"],))
    db._conn.commit()
    client.cookies.set("sb_session", auth.make_session_token(row["id"]))
    return row


def test_admin_search_with_no_results_shows_first_visit_cta(client, db):
    _login_admin(client, db)
    r = client.get("/admin?q=nobody@example.com")
    assert r.status_code == 200
    assert 'name="target" value="/kiosk/new"' in r.text


def test_admin_search_with_results_does_not_show_cta(client, db):
    row = _login_admin(client, db)
    r = client.get(f"/admin?q={row['email']}")
    assert 'name="target" value="/kiosk/new"' not in r.text


def test_kiosk_start_with_new_target_jumps_straight_to_first_visit_form(client, db):
    _login_admin(client, db)
    r = client.post("/admin/kiosk/start", data={"target": "/kiosk/new"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/kiosk/new"


def test_kiosk_start_rejects_unknown_target(client, db):
    _login_admin(client, db)
    r = client.post("/admin/kiosk/start", data={"target": "/evil"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/kiosk"
