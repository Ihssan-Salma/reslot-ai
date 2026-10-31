import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    DUMMY_HASH,
    digest_token,
    hash_password,
    password_hasher,
    verify_password,
)
from app.models import AuthSession, User
from app.repositories.auth import AuthRepository
from app.schemas.auth import Credentials


class AuthService:
    def __init__(self, db: Session):
        self.db = db
        self.repository = AuthRepository(db)

    def register(self, data: Credentials) -> User:
        user = User(email=str(data.email), password_hash=hash_password(data.password))
        try:
            with self.db.begin_nested():
                self.db.add(user)
                self.db.flush()
        except IntegrityError:
            raise HTTPException(409, "An account with this email already exists") from None
        return user

    def login(self, data: Credentials, previous_token: str | None) -> tuple[User, str]:
        user = self.repository.by_email(str(data.email))
        valid = verify_password(data.password, user.password_hash if user else DUMMY_HASH)
        if not user or not valid:
            raise HTTPException(401, "Incorrect email or password")
        if password_hasher.check_needs_rehash(user.password_hash):
            user.password_hash = hash_password(data.password)
        if previous_token:
            old_session = self.repository.valid_session(digest_token(previous_token))
            if old_session:
                self.db.delete(old_session)
        token = secrets.token_urlsafe(32)
        self.db.add(
            AuthSession(
                token_hash=digest_token(token),
                user_id=user.id,
                expires_at=datetime.now(UTC)
                + timedelta(seconds=get_settings().session_ttl_seconds),
            )
        )
        self.db.flush()
        return user, token
