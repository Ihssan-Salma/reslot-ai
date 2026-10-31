from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.integrations.google.errors import GoogleError

BASE_SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
)
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SCOPES = (*BASE_SCOPES, SHEETS_SCOPE)
AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"


def token_cipher() -> Fernet:
    key = get_settings().google_token_encryption_key.get_secret_value()
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise GoogleError(
            "google_not_configured", "Google token encryption is not configured", 503
        ) from None


def require_configuration() -> None:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret.get_secret_value():
        raise GoogleError("google_not_configured", "Google OAuth is not configured", 503)
    token_cipher()


def configured() -> bool:
    try:
        require_configuration()
        return True
    except GoogleError:
        return False


def encrypt_token(value: str) -> str:
    return token_cipher().encrypt(value.encode()).decode()


def decrypt_token(value: str) -> str:
    try:
        return token_cipher().decrypt(value.encode()).decode()
    except InvalidToken:
        raise GoogleError(
            "encryption_key_error", "The stored Google token cannot be decrypted", 503
        ) from None


def normalize_scopes(value: str) -> list[str]:
    return sorted(
        {
            "email" if item == "https://www.googleapis.com/auth/userinfo.email" else item
            for item in value.split()
        }
    )
