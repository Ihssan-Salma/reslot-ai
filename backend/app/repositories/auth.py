from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuthSession, User


class AuthRepository:
    def __init__(self, db: Session):
        self.db = db

    def by_email(self, email: str) -> User | None:
        return self.db.scalar(select(User).where(User.email == email))

    def valid_session(self, token_hash: str) -> AuthSession | None:
        return self.db.scalar(
            select(AuthSession)
            .where(
                AuthSession.token_hash == token_hash,
                AuthSession.expires_at > datetime.now(UTC),
            )
            .with_for_update()
        )
