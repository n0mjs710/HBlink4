"""Password, session, CSRF, and bounded login throttling helpers."""

import hashlib
import hmac
import secrets
import time
from collections import OrderedDict

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError


PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
SESSION_COOKIE = "bueno_admin_session"
LOGIN_CSRF_COOKIE = "bueno_admin_login_csrf"
LOGIN_CSRF_LIFETIME = 600


def password_hash(password):
    return PASSWORD_HASHER.hash(password)


def password_valid(stored_hash, password):
    try:
        return PASSWORD_HASHER.verify(stored_hash, password)
    except (InvalidHashError, VerifyMismatchError, VerificationError):
        return False


def session_digest(secret, token):
    return hmac.new(secret, token.encode("ascii"), hashlib.sha256).hexdigest()


def csrf_digest(secret, purpose, value):
    message = (purpose + ":" + value).encode("ascii")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def new_login_csrf(secret):
    nonce = f"{int(time.time())}.{secrets.token_urlsafe(24)}"
    return nonce, csrf_digest(secret, "login", nonce)


def valid_login_csrf(secret, nonce, submitted):
    try:
        stamp = int(nonce.split(".", 1)[0])
        if not 0 <= time.time() - stamp <= LOGIN_CSRF_LIFETIME:
            return False
        return hmac.compare_digest(csrf_digest(secret, "login", nonce), submitted)
    except (AttributeError, TypeError, ValueError, UnicodeError):
        return False


class LoginLimiter:
    """Per-IP and per-username rolling windows with a fixed memory ceiling."""

    def __init__(self, window=900, max_entries=1024):
        self.window = window
        self.max_entries = max_entries
        self.attempts = OrderedDict()

    def _prune(self, now):
        for key in list(self.attempts):
            recent = [stamp for stamp in self.attempts[key] if now - stamp < self.window]
            if recent:
                self.attempts[key] = recent
            else:
                del self.attempts[key]

    def limited(self, ip, username):
        now = time.monotonic()
        self._prune(now)
        return (len(self.attempts.get(("ip", ip), ())) >= 10 or
                len(self.attempts.get(("user", username.upper()), ())) >= 5)

    def failure(self, ip, username):
        now = time.monotonic()
        self._prune(now)
        for key in (("ip", ip), ("user", username.upper())):
            self.attempts.setdefault(key, []).append(now)
            self.attempts.move_to_end(key)
        while len(self.attempts) > self.max_entries:
            self.attempts.popitem(last=False)

    def success(self, ip, username):
        self.attempts.pop(("ip", ip), None)
        self.attempts.pop(("user", username.upper()), None)
