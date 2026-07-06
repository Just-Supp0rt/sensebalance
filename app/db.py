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
        self._conn.commit()

    def _migrate(self):
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(profile)").fetchall()
        }
        for col, decl in _PROFILE_MIGRATIONS.items():
            if col not in existing:
                self._conn.execute(f"ALTER TABLE profile ADD COLUMN {col} {decl}")

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
