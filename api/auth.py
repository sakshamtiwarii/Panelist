"""Authentication: password hashing, signed sessions, and the two role gates.

    coordinator  full access — solve, replan, and apply a replan
    viewer       read-only — board, metrics, diagnostics, and may ask for a
                 proposal, but cannot commit one

Proposing mutates nothing while applying does, so the permission boundary sits
at the state change rather than at the whole feature.

Passwords are scrypt hashes with a 16-byte per-user salt. Sessions are
HMAC-SHA256-signed tokens carrying username, role and expiry, compared in
constant time, and ride in an httpOnly cookie so page JavaScript cannot read
them.

PANELIST_SECRET_KEY must be set in a real deployment. The fallback is generated
per-process and invalidates every session on restart, so an unset secret is
noticed rather than silently insecure.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Cookie, Depends, HTTPException

COOKIE_NAME = "panelist_session"
SESSION_HOURS = 12

# Marks the session cookie HTTPS-only. Off locally, where http://localhost
# would drop the cookie silently and make login look broken.
COOKIE_SECURE = os.environ.get("PANELIST_COOKIE_SECURE", "0") == "1"
# "lax" holds while the page and the API share an origin, which is how the
# dashboard proxies them. A split-origin deployment needs "none", which
# additionally requires COOKIE_SECURE.
COOKIE_SAMESITE = os.environ.get("PANELIST_COOKIE_SAMESITE", "lax")

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}

_env_secret = os.environ.get("PANELIST_SECRET_KEY")
if _env_secret:
    SECRET = _env_secret.encode()
else:
    SECRET = secrets.token_bytes(32)
    print("[auth] PANELIST_SECRET_KEY unset — using a per-process key; "
          "sessions will not survive a restart")


# --- passwords -------------------------------------------------------------

def hash_password(password: str, salt: bytes = None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return salt, digest


def verify_password(password: str, salt: bytes, expected: bytes) -> bool:
    candidate = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return hmac.compare_digest(candidate, bytes(expected))


# --- session tokens --------------------------------------------------------

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(username: str, role: str) -> str:
    payload = {
        "sub": username,
        "role": role,
        "exp": int(time.time()) + SESSION_HOURS * 3600,
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token: str):
    """Return the payload, or None if the token is forged, malformed or stale."""
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    # Constant-time: a fast reject would leak signature bytes by timing.
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


# --- FastAPI dependencies --------------------------------------------------

def current_user(panelist_session: str = Cookie(default=None)):
    """Resolve the signed-in user, or 401."""
    if not panelist_session:
        raise HTTPException(401, "Not signed in.")
    payload = read_token(panelist_session)
    if not payload:
        raise HTTPException(401, "Session expired or invalid — sign in again.")
    return {"username": payload["sub"], "role": payload["role"]}


def require_coordinator(user=Depends(current_user)):
    """Gate the operations that actually change the schedule."""
    if user["role"] != "coordinator":
        raise HTTPException(
            403,
            "This action changes the live schedule and needs a coordinator "
            "account. You are signed in as a viewer.",
        )
    return user


# --- demo accounts ---------------------------------------------------------
#
# Seeded on first start. Evaluation credentials for a synthetic dataset,
# published in the README on purpose; PANELIST_SEED_USERS=0 disables seeding.
DEMO_USERS = [
    ("coordinator", "Priya Raman · Placement Coordinator", "coordinator",
     "placement2026"),
    ("viewer", "Reviewer · Read-only", "viewer", "review2026"),
]


def seed_demo_users(store):
    if os.environ.get("PANELIST_SEED_USERS", "1") != "1":
        return []
    created = []
    for username, display, role, password in DEMO_USERS:
        if store.get_user(username):
            continue
        salt, digest = hash_password(password)
        store.put_user(username, display, role, salt, digest)
        created.append(username)
    return created
