from __future__ import annotations

from app import identity


def _make_admin(db, name: str, pin: str, email: str = "aom@sensebalance.cz"):
    db.ensure_email_user(email)
    row = db.get_user_by_email(email)
    db._conn.execute("UPDATE user SET is_admin=1, name=? WHERE id=?", (name, row["id"]))
    db._conn.commit()
    pin_hash, pin_salt = identity.hash_pin(pin)
    db.set_pin(row["id"], pin_hash, pin_salt)
    return row


def test_correct_name_and_pin_logs_in(client, db):
    _make_admin(db, "Kuba", "0209")
    r = client.post(
        "/auth/staff-login", data={"name": "Kuba", "pin": "0209"}, follow_redirects=False
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/profile"
    assert "sb_session" in client.cookies


def test_login_is_case_insensitive_on_name(client, db):
    _make_admin(db, "Kuba", "0209")
    r = client.post(
        "/auth/staff-login", data={"name": "kuba", "pin": "0209"}, follow_redirects=False
    )
    assert r.status_code == 302


def test_wrong_pin_and_unknown_name_return_identical_response(client, db):
    _make_admin(db, "Kuba", "0209")
    wrong = client.post("/auth/staff-login", data={"name": "Kuba", "pin": "0000"})
    unknown = client.post("/auth/staff-login", data={"name": "Nobody", "pin": "0000"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.text == unknown.text


def test_sixth_attempt_is_locked_out(client, db):
    _make_admin(db, "Kuba", "0209")
    for _ in range(5):
        r = client.post("/auth/staff-login", data={"name": "Kuba", "pin": "0000"})
        assert r.status_code == 401
    locked = client.post("/auth/staff-login", data={"name": "Kuba", "pin": "0209"})
    assert locked.status_code == 429


def test_non_admin_account_cannot_log_in_via_staff_login(client, db):
    db.ensure_email_user("test@example.com")
    row = db.get_user_by_email("test@example.com")
    pin_hash, pin_salt = identity.hash_pin("1111")
    db.set_pin(row["id"], pin_hash, pin_salt)
    r = client.post("/auth/staff-login", data={"name": row["name"] or "test", "pin": "1111"})
    assert r.status_code == 401
