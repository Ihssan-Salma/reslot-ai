from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.domain import BookingError
from app.models.booking import TenantRecord


class BookingRepository:
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    def lock(self) -> None:
        # A tenant-wide transaction gate also serializes configuration with booking changes.
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "booking:" + str(self.tenant_id)},
        )

    def get[T: TenantRecord](self, model: type[T], identifier: UUID, *, lock: bool = False) -> T:
        query = select(model).where(model.tenant_id == self.tenant_id, model.id == identifier)
        if lock:
            query = query.with_for_update()
        row = self.db.scalar(query)
        if row is None or getattr(row, "deleted_at", None) is not None:
            raise BookingError("Record not found for this business", 404)
        return row

    def list[T: TenantRecord](self, model: type[T], limit: int = 200, offset: int = 0) -> list[T]:
        return list(
            self.db.scalars(
                select(model)
                .where(model.tenant_id == self.tenant_id)
                .order_by(model.id)
                .limit(limit)
                .offset(offset)
            )
        )
