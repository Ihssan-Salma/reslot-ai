import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type
from fastapi import HTTPException, Request, Response

from app.core.config import get_settings

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, type=Type.ID)
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_cookie_name() -> str:
    return "__Host-reslot_session" if get_settings().cookie_secure else "reslot_session"


def csrf_cookie_name() -> str:
    return "__Host-reslot_csrf" if get_settings().cookie_secure else "reslot_csrf"


def set_private_cookie(response: Response, name: str, value: str) -> None:
    response.set_cookie(
        name,
        value,
        max_age=get_settings().session_ttl_seconds,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
        path="/",
    )


def csrf_token(nonce: str, session_token: str) -> str:
    # The random HttpOnly nonce is the HMAC key. Binding prevents login/session fixation CSRF.
    return hmac.new(nonce.encode(), session_token.encode(), hashlib.sha256).hexdigest()


def issue_csrf(request: Request, response: Response) -> str:
    nonce = request.cookies.get(csrf_cookie_name()) or secrets.token_urlsafe(32)
    set_private_cookie(response, csrf_cookie_name(), nonce)
    return csrf_token(nonce, request.cookies.get(session_cookie_name(), ""))


def require_csrf(request: Request) -> None:
    if request.headers.get("origin") not in get_settings().cors_origins:
        raise HTTPException(403, "Untrusted request origin")
    nonce = request.cookies.get(csrf_cookie_name(), "")
    supplied = request.headers.get("x-csrf-token", "")
    expected = csrf_token(nonce, request.cookies.get(session_cookie_name(), ""))
    if not nonce or not supplied or not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise HTTPException(403, "Invalid CSRF token")
