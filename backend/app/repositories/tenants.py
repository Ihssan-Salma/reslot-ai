from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Tenant, TenantMembership
from app.schemas.auth import TenantCreate


class TenantRepository:
    """RLS remains mandatory even when explicit application predicates are present."""

    def __init__(self, db: Session, user_id: UUID):
        self.db = db
        self.user_id = user_id

    def membership(self, tenant_id: UUID) -> TenantMembership | None:
        return self.db.scalar(
            select(TenantMembership).where(
                TenantMembership.user_id == self.user_id,
                TenantMembership.tenant_id == tenant_id,
            )
        )

    def tenant(self, tenant_id: UUID) -> Tenant | None:
        return self.db.scalar(select(Tenant).where(Tenant.id == tenant_id))

    def list_memberships(self) -> list[tuple[Tenant, TenantMembership]]:
        rows = self.db.execute(
            select(Tenant, TenantMembership)
            .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
            .where(TenantMembership.user_id == self.user_id)
            .order_by(Tenant.created_at)
        ).all()
        return [(tenant, membership) for tenant, membership in rows]

    def create(self, data: TenantCreate) -> UUID:
        # Narrow SECURITY DEFINER entry point bootstraps tenant + owner atomically.
        result = self.db.scalar(
            text("SELECT public.create_business(:name, :business_type, :timezone)"),
            data.model_dump(),
        )
        return UUID(str(result))
