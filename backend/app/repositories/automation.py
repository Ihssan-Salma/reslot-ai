from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.automation import CalendarEventSnapshot, IdempotencyRecord, SpreadsheetConfiguration


class AutomationRepository:
    def __init__(self, db: Session, tenant_id: UUID):
        self.db, self.tenant_id = db, tenant_id

    def spreadsheet(self) -> SpreadsheetConfiguration | None:
        return self.db.scalar(
            select(SpreadsheetConfiguration).where(
                SpreadsheetConfiguration.tenant_id == self.tenant_id
            )
        )

    def operation(self, key: str) -> IdempotencyRecord | None:
        return self.db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.tenant_id == self.tenant_id,
                IdempotencyRecord.operation_key == key,
            )
        )

    def snapshot(self, calendar_id: str, event_id: str) -> CalendarEventSnapshot | None:
        return self.db.scalar(
            select(CalendarEventSnapshot).where(
                CalendarEventSnapshot.tenant_id == self.tenant_id,
                CalendarEventSnapshot.calendar_id == calendar_id,
                CalendarEventSnapshot.event_id == event_id,
            )
        )
