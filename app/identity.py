"""Identity helpers for the kiosk return flow: phone normalization, PIN hashing,
and a small in-memory lockout guard against PIN brute-forcing.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time

# pbkdf2 rounds — deliberately high since PINs are only 4 digits (10k keyspace).
_PBKDF2_ROUNDS = 200_000
_LOCKOUT_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 15 * 60

# identifier -> (fail_count, locked_until_epoch)
_attempts: dict[str, tuple[int, float]] = {}


def normalize_phone(raw: str) -> str:
    """Keep digits only; assume a bare 9-digit number is Czech and prefix +420."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 9:
        digits = "420" + digits
    return digits


def normalize_identifier(raw: str) -> str:
    raw = (raw or "").strip()
    if "@" in raw:
        return raw.lower()
    return normalize_phone(raw)


def hash_pin(pin: str, salt: bytes | None = None) -> tuple[str, str]:
    """Returns (hash_hex, salt_hex)."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return digest.hex(), salt.hex()


def verify_pin_hash(pin: str, pin_hash: str, pin_salt: str) -> bool:
    if not pin_hash or not pin_salt:
        # Still do the work so timing doesn't reveal "user has no PIN".
        hash_pin(pin, secrets.token_bytes(16))
        return False
    digest, _ = hash_pin(pin, bytes.fromhex(pin_salt))
    return secrets.compare_digest(digest, pin_hash)


def is_valid_pin(pin: str) -> bool:
    return bool(re.fullmatch(r"\d{4}", pin or ""))


# --- lockout ---

def is_locked(identifier: str) -> bool:
    entry = _attempts.get(identifier)
    if not entry:
        return False
    _, locked_until = entry
    return time.monotonic() < locked_until


def record_failure(identifier: str) -> None:
    count, locked_until = _attempts.get(identifier, (0, 0.0))
    count += 1
    if count >= _LOCKOUT_MAX_ATTEMPTS:
        locked_until = time.monotonic() + _LOCKOUT_SECONDS
    _attempts[identifier] = (count, locked_until)


def record_success(identifier: str) -> None:
    _attempts.pop(identifier, None)
