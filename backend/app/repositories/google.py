from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models.google import BusinessCalendar, GoogleConnection, GoogleOAuthState


class GoogleRepository:
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    def lock(self) -> None:
        # Serialize refresh/reconnect/disconnect across different sessions of the same tenant.
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant, 0))"),
            {"tenant": str(self.tenant_id)},
        )

    def connection(self) -> GoogleConnection | None:
        return self.db.scalar(
            select(GoogleConnection).where(GoogleConnection.tenant_id == self.tenant_id)
        )

    def calendars(self) -> list[BusinessCalendar]:
        return list(
            self.db.scalars(
                select(BusinessCalendar)
                .where(BusinessCalendar.tenant_id == self.tenant_id)
                .order_by(BusinessCalendar.calendar_name)
            )
        )

    def clear_calendars(self) -> None:
        self.db.execute(
            delete(BusinessCalendar).where(BusinessCalendar.tenant_id == self.tenant_id)
        )

    def clear_states(self) -> None:
        self.db.execute(
            delete(GoogleOAuthState).where(GoogleOAuthState.tenant_id == self.tenant_id)
        )

    def state(self, state_hash: str) -> GoogleOAuthState | None:
        return self.db.scalar(
            select(GoogleOAuthState)
            .where(
                GoogleOAuthState.tenant_id == self.tenant_id,
                GoogleOAuthState.state_hash == state_hash,
            )
            .with_for_update()
        )
