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


def record_failure(identifier: str) -> bool:
    """Records a failed PIN attempt. Returns True the moment this failure
    crosses the lockout threshold (so callers can fire a one-time alert
    instead of one email per attempt)."""
    count, locked_until = _attempts.get(identifier, (0, 0.0))
    was_locked = time.monotonic() < locked_until
    count += 1
    newly_locked = False
    if count >= _LOCKOUT_MAX_ATTEMPTS and not was_locked:
        locked_until = time.monotonic() + _LOCKOUT_SECONDS
        newly_locked = True
    _attempts[identifier] = (count, locked_until)
    return newly_locked


def record_success(identifier: str) -> None:
    _attempts.pop(identifier, None)


# --- simple per-key request throttle (defense in depth beyond PIN lockout —
# bounds how many searches even a *correctly*-PIN'd session can run, so a
# leaked/guessed PIN can't be used to enumerate the whole client list) ---

_MAX_REQUESTS_PER_WINDOW = 30
_WINDOW_SECONDS = 3600

# key -> (window_start_epoch, count)
_windows: dict[str, tuple[float, int]] = {}


def check_rate_limit(key: str) -> bool:
    """Returns True if this call is within the limit (and counts it), False
    if the caller should be rejected."""
    now = time.monotonic()
    start, count = _windows.get(key, (now, 0))
    if now - start >= _WINDOW_SECONDS:
        start, count = now, 0
    count += 1
    _windows[key] = (start, count)
    return count <= _MAX_REQUESTS_PER_WINDOW


def client_ip(request) -> str:
    """Best-effort client IP for lockout/rate-limit keys. If this app runs
    behind a reverse proxy, that proxy MUST set X-Forwarded-For — otherwise
    every request looks like it comes from the proxy and this degrades to
    one shared bucket for everyone (same as no per-IP keying at all, not
    worse, but not the intended protection either)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
