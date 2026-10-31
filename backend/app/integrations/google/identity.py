import hmac
from typing import Any

import jwt

from app.core.config import get_settings
from app.core.security import digest_token
from app.integrations.google.config import JWKS_URL
from app.integrations.google.errors import GoogleError
from app.integrations.google.http import GoogleHTTP


def verify_identity(encoded: str, nonce_hash: str, http: GoogleHTTP) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(encoded)
        response = http.request("GET", JWKS_URL)
        if response.status_code != 200:
            raise GoogleError(
                "google_unavailable", "Google identity verification is unavailable", 503
            )
        keys = http.json(response).get("keys", [])
        jwk = next(key for key in keys if key.get("kid") == header.get("kid"))
        if jwk.get("kty") != "RSA" or header.get("alg") != "RS256":
            raise ValueError("Unsupported signing algorithm")
        claims = jwt.decode(
            encoded,
            jwt.PyJWK.from_dict(jwk).key,
            algorithms=["RS256"],
            audience=get_settings().google_client_id,
            issuer=["https://accounts.google.com", "accounts.google.com"],
            options={
                "require": ["exp", "iat", "iss", "aud", "sub", "nonce", "email", "email_verified"]
            },
        )
        audience = claims.get("aud")
        if isinstance(audience, list) and len(audience) > 1 and not claims.get("azp"):
            raise ValueError("Multiple audiences require an authorized party")
        if claims.get("azp", get_settings().google_client_id) != get_settings().google_client_id:
            raise ValueError("Invalid authorized party")
        if not claims["sub"] or not isinstance(claims["email"], str) or not claims["email"]:
            raise ValueError("Missing identity")
        if claims["email_verified"] is not True or not hmac.compare_digest(
            digest_token(str(claims["nonce"])), nonce_hash
        ):
            raise ValueError("Unverified identity or nonce")
        return dict(claims)
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, StopIteration):
        raise GoogleError(
            "invalid_google_identity", "Google identity could not be verified", 400
        ) from None
