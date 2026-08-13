from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS user (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL DEFAULT '',
    locale      TEXT NOT NULL DEFAULT 'cs',
    provider    TEXT NOT NULL DEFAULT 'email',
    google_sub  TEXT,
    is_admin    INTEGER NOT NULL DEFAULT 0,
    consent_at  TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile (
    user_id        INTEGER PRIMARY KEY REFERENCES user(id) ON DELETE CASCADE,
    focus_zones    TEXT NOT NULL DEFAULT '[]',
    avoid_zones    TEXT NOT NULL DEFAULT '[]',
    pressure       TEXT NOT NULL DEFAULT 'medium',
    problem_tags   TEXT NOT NULL DEFAULT '[]',
    health_flags   TEXT NOT NULL DEFAULT '[]',
    oil_allergies  TEXT NOT NULL DEFAULT '',
    note_original  TEXT NOT NULL DEFAULT '',
    note_lang      TEXT NOT NULL DEFAULT '',
    note_th        TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS magic_token (
    token      TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT
);

CREATE TABLE IF NOT EXISTS visit (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
    created_at       TEXT NOT NULL,
    kind             TEXT NOT NULL,
    changed_sections TEXT NOT NULL DEFAULT '[]',
    snapshot         TEXT NOT NULL,
    signature_png    TEXT NOT NULL,
    consent_at       TEXT NOT NULL,
    consent_version  TEXT NOT NULL DEFAULT 'v1'
);
CREATE INDEX IF NOT EXISTS idx_visit_user ON visit(user_id, created_at DESC);
"""

# Intake-form columns added after initial release; applied via _migrate().
_PROFILE_MIGRATIONS = {
    "phone": "TEXT NOT NULL DEFAULT ''",
    "has_health_problems": "TEXT NOT NULL DEFAULT ''",
    "health_problems": "TEXT NOT NULL DEFAULT ''",
    "pregnancy": "TEXT NOT NULL DEFAULT ''",
    "blood_pressure": "TEXT NOT NULL DEFAULT ''",
    "exercise": "TEXT NOT NULL DEFAULT ''",
    "exercise_detail": "TEXT NOT NULL DEFAULT ''",
    "recent_surgery": "TEXT NOT NULL DEFAULT ''",
    "surgery_detail": "TEXT NOT NULL DEFAULT ''",
    "consent_at": "TEXT",
    "signature_png": "TEXT NOT NULL DEFAULT ''",
    "note_en": "TEXT NOT NULL DEFAULT ''",
}

_USER_MIGRATIONS = {
    "pin_hash": "TEXT NOT NULL DEFAULT ''",
    "pin_salt": "TEXT NOT NULL DEFAULT ''",
    "phone": "TEXT NOT NULL DEFAULT ''",
}

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_CREATE_SQL)
        self._migrate()
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_phone "
            "ON user(phone) WHERE phone != ''"
        )
        self._backfill_visits()
        self._conn.commit()

    def _migrate(self):
        for table, migrations in (
            ("profile", _PROFILE_MIGRATIONS),
            ("user", _USER_MIGRATIONS),
        ):
            existing = {
                row["name"]
                for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for col, decl in migrations.items():
                if col not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def _backfill_visits(self):
        """Existing signed profiles predate the `visit` table — give each one a
        single 'first' visit row so admin history isn't empty for old clients."""
        rows = self._conn.execute(
            "SELECT * FROM profile p WHERE p.signature_png != '' AND p.consent_at IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM visit v WHERE v.user_id = p.user_id)"
        ).fetchall()
        for row in rows:
            snapshot = {k: row[k] for k in row.keys()}
            self._conn.execute(
                "INSERT INTO visit (user_id, created_at, kind, changed_sections, "
                "snapshot, signature_png, consent_at, consent_version) "
                "VALUES (?, ?, 'first', '[]', ?, ?, ?, 'v1')",
                (
                    row["user_id"],
                    row["consent_at"],
                    json.dumps(snapshot),
                    row["signature_png"],
                    row["consent_at"],
                ),
            )

    # --- users ---

    def get_user_by_email(self, email: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM user WHERE email = ?", (email,)
        ).fetchone()

    def get_user_by_id(self, user_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM user WHERE id = ?", (user_id,)
        ).fetchone()

    def get_user_by_google_sub(self, sub: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM user WHERE google_sub = ?", (sub,)
        ).fetchone()

    def get_admin_by_name(self, name: str) -> sqlite3.Row | None:
        """Case-insensitive exact match, restricted to admin accounts — used by
        the staff name+PIN login. Assumes distinct staff display names."""
        return self._conn.execute(
            "SELECT * FROM user WHERE LOWER(name) = LOWER(?) AND is_admin = 1", (name,)
        ).fetchone()

    def upsert_google_user(self, email: str, name: str, sub: str) -> sqlite3.Row:
        existing = self.get_user_by_google_sub(sub)
        if existing:
            return existing
        by_email = self.get_user_by_email(email)
        if by_email:
            self._conn.execute(
                "UPDATE user SET google_sub=?, provider='google', name=? WHERE id=?",
                (sub, name or by_email["name"], by_email["id"]),
            )
            self._conn.commit()
            return self.get_user_by_id(by_email["id"])
        self._conn.execute(
            "INSERT INTO user (email, name, provider, google_sub, consent_at, created_at) "
            "VALUES (?, ?, 'google', ?, ?, ?)",
            (email, name, sub, _now(), _now()),
        )
        self._conn.commit()
        return self.get_user_by_email(email)

    def ensure_email_user(self, email: str) -> sqlite3.Row:
        row = self.get_user_by_email(email)
        if row:
            return row
        self._conn.execute(
            "INSERT INTO user (email, provider, consent_at, created_at) VALUES (?, 'email', ?, ?)",
            (email, _now(), _now()),
        )
        self._conn.commit()
        return self.get_user_by_email(email)

    def set_user_name(self, user_id: int, name: str):
        self._conn.execute("UPDATE user SET name=? WHERE id=?", (name, user_id))
        self._conn.commit()

    def get_user_by_phone(self, phone: str) -> sqlite3.Row | None:
        if not phone:
            return None
        return self._conn.execute(
            "SELECT * FROM user WHERE phone = ?", (phone,)
        ).fetchone()

    def find_user_by_identifier(self, identifier: str) -> sqlite3.Row | None:
        """identifier is an already-normalized email or phone (see app.identity)."""
        if "@" in identifier:
            return self.get_user_by_email(identifier)
        return self.get_user_by_phone(identifier)

    def set_pin(self, user_id: int, pin_hash: str, pin_salt: str):
        self._conn.execute(
            "UPDATE user SET pin_hash=?, pin_salt=? WHERE id=?",
            (pin_hash, pin_salt, user_id),
        )
        self._conn.commit()

    def ensure_kiosk_user(self, email: str, phone: str) -> sqlite3.Row:
        """Create-or-fetch a user for the kiosk 'first visit' flow, keyed by email."""
        row = self.get_user_by_email(email)
        if row:
            if phone and not row["phone"]:
                try:
                    self._conn.execute("UPDATE user SET phone=? WHERE id=?", (phone, row["id"]))
                    self._conn.commit()
                    row = self.get_user_by_id(row["id"])
                except sqlite3.IntegrityError:
                    # Another account already claimed this phone (e.g. a couple
                    # sharing one number). Keep this account's record as-is
                    # rather than failing the whole kiosk submission over it.
                    self._conn.rollback()
            return row
        try:
            self._conn.execute(
                "INSERT INTO user (email, phone, provider, consent_at, created_at) "
                "VALUES (?, ?, 'email', ?, ?)",
                (email, phone, _now(), _now()),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            self._conn.rollback()
            self._conn.execute(
                "INSERT INTO user (email, provider, consent_at, created_at) "
                "VALUES (?, 'email', ?, ?)",
                (email, _now(), _now()),
            )
            self._conn.commit()
        return self.get_user_by_email(email)

    def delete_user(self, user_id: int):
        self._conn.execute("DELETE FROM user WHERE id=?", (user_id,))
        self._conn.commit()

    def list_users(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT u.*, p.updated_at as profile_updated FROM user u "
            "LEFT JOIN profile p ON p.user_id = u.id "
            "ORDER BY u.created_at DESC"
        ).fetchall()

    # --- profiles ---

    def get_profile(self, user_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM profile WHERE user_id=?", (user_id,)
        ).fetchone()

    def save_profile(self, user_id: int, data: dict):
        self._save_profile_no_commit(user_id, data)
        self._conn.commit()

    def _save_profile_no_commit(self, user_id: int, data: dict):
        existing = self.get_profile(user_id)
        cols = {
            "focus_zones": json.dumps(data.get("focus_zones", [])),
            "avoid_zones": json.dumps(data.get("avoid_zones", [])),
            "pressure": data.get("pressure", "medium"),
            "problem_tags": json.dumps(data.get("problem_tags", [])),
            "health_flags": json.dumps(data.get("health_flags", [])),
            "oil_allergies": data.get("oil_allergies", ""),
            "note_original": data.get("note_original", ""),
            "note_lang": data.get("note_lang", ""),
            "note_th": data.get("note_th", ""),
            "note_en": data.get("note_en", ""),
            "phone": data.get("phone", ""),
            "has_health_problems": data.get("has_health_problems", ""),
            "health_problems": data.get("health_problems", ""),
            "pregnancy": data.get("pregnancy", ""),
            "blood_pressure": data.get("blood_pressure", ""),
            "exercise": data.get("exercise", ""),
            "exercise_detail": data.get("exercise_detail", ""),
            "recent_surgery": data.get("recent_surgery", ""),
            "surgery_detail": data.get("surgery_detail", ""),
            "signature_png": data.get("signature_png", ""),
            "updated_at": _now(),
        }
        # consent timestamp is set once, on the first consenting save
        if data.get("consent") and not (existing and existing["consent_at"]):
            cols["consent_at"] = _now()
        if existing:
            assignments = ", ".join(f"{c}=?" for c in cols)
            self._conn.execute(
                f"UPDATE profile SET {assignments} WHERE user_id=?",
                (*cols.values(), user_id),
            )
        else:
            names = ", ".join(["user_id", *cols])
            marks = ", ".join("?" * (len(cols) + 1))
            self._conn.execute(
                f"INSERT INTO profile ({names}) VALUES ({marks})",
                (user_id, *cols.values()),
            )

    def record_submission(
        self, user_id: int, data: dict, kind: str, changed_sections: list[str]
    ) -> int:
        """Writes an immutable `visit` snapshot and updates the `profile` "latest
        state" row in a single transaction — a visit can never exist without a
        matching profile update, or vice versa."""
        now = _now()
        consent_at = now if data.get("consent") else (data.get("consent_at") or now)
        with self._conn:
            self._save_profile_no_commit(user_id, data)
            cur = self._conn.execute(
                "INSERT INTO visit (user_id, created_at, kind, changed_sections, "
                "snapshot, signature_png, consent_at, consent_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'v1')",
                (
                    user_id,
                    now,
                    kind,
                    json.dumps(changed_sections),
                    json.dumps(data),
                    data.get("signature_png", ""),
                    consent_at,
                ),
            )
            return cur.lastrowid

    def list_visits(self, user_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM visit WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        ).fetchall()

    def get_visit(self, visit_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM visit WHERE id=?", (visit_id,)
        ).fetchone()

    def latest_visit(self, user_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM visit WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    def update_profile_translation(self, user_id: int, lang_field: str, text: str):
        if lang_field not in ("note_th", "note_en"):
            raise ValueError(lang_field)
        self._conn.execute(
            f"UPDATE profile SET {lang_field}=? WHERE user_id=?", (text, user_id)
        )
        self._conn.commit()

    def update_visit_translation(self, visit_id: int, lang_field: str, text: str):
        """Best-effort background patch of a visit snapshot once async translation
        completes. `lang_field` is 'note_th' or 'note_en'."""
        if lang_field not in ("note_th", "note_en"):
            raise ValueError(lang_field)
        row = self.get_visit(visit_id)
        if not row:
            return
        snapshot = json.loads(row["snapshot"])
        snapshot[lang_field] = text
        self._conn.execute(
            "UPDATE visit SET snapshot=? WHERE id=?", (json.dumps(snapshot), visit_id)
        )
        self._conn.commit()

    # --- magic tokens ---

    def create_magic_token(self, token: str, email: str, expires_at: str):
        self._conn.execute(
            "INSERT INTO magic_token (token, email, expires_at) VALUES (?,?,?)",
            (token, email, expires_at),
        )
        self._conn.commit()

    def consume_magic_token(self, token: str) -> str | None:
        """Returns email if token is valid and unused, else None."""
        row = self._conn.execute(
            "SELECT email, expires_at, used_at FROM magic_token WHERE token=?", (token,)
        ).fetchone()
        if not row or row["used_at"]:
            return None
        from datetime import datetime, timezone
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            return None
        self._conn.execute(
            "UPDATE magic_token SET used_at=? WHERE token=?", (_now(), token)
        )
        self._conn.commit()
        return row["email"]

    def cleanup_old_tokens(self):
        self._conn.execute(
            "DELETE FROM magic_token WHERE expires_at < ?",
            (datetime.now(timezone.utc).isoformat(),),
        )
        self._conn.commit()
