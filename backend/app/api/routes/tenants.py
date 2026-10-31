from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import (
    CurrentTenant,
    CurrentUser,
    Db,
    TenantContext,
    authorize_tenant,
)
from app.core.security import require_csrf
from app.db.session import set_tenant_context
from app.repositories.tenants import TenantRepository
from app.schemas.auth import TenantCreate, TenantProfile, TenantSwitch

router = APIRouter(prefix="/api/v1", tags=["businesses"])


def tenant_profile(context: TenantContext) -> TenantProfile:
    return TenantProfile.model_validate(
        {
            **{
                name: getattr(context.tenant, name)
                for name in ("id", "name", "business_type", "timezone", "status", "created_at")
            },
            "role": context.membership.role,
        }
    )


@router.get("/tenants", response_model=list[TenantProfile])
def list_tenants(auth: CurrentUser, db: Db) -> list[TenantProfile]:
    return [
        tenant_profile(TenantContext(tenant, membership))
        for tenant, membership in TenantRepository(db, auth.user.id).list_memberships()
    ]


@router.post(
    "/tenants", response_model=TenantProfile, status_code=201, dependencies=[Depends(require_csrf)]
)
def create_tenant(data: TenantCreate, auth: CurrentUser, db: Db) -> TenantProfile:
    tenant_id = TenantRepository(db, auth.user.id).create(data)
    context = authorize_tenant(db, auth, tenant_id)
    auth.login.active_tenant_id = context.tenant.id
    return tenant_profile(context)


@router.post("/tenants/switch", response_model=TenantProfile, dependencies=[Depends(require_csrf)])
def switch_tenant(data: TenantSwitch, auth: CurrentUser, db: Db) -> TenantProfile:
    set_tenant_context(db, None)
    context = authorize_tenant(db, auth, data.tenant_id)
    auth.login.active_tenant_id = context.tenant.id
    return tenant_profile(context)


@router.get("/tenants/{tenant_id}/dashboard", response_model=TenantProfile)
def tenant_dashboard(tenant_id: UUID, auth: CurrentUser, db: Db) -> TenantProfile:
    return tenant_profile(authorize_tenant(db, auth, tenant_id))


@router.get("/dashboard", response_model=TenantProfile)
def dashboard(context: CurrentTenant) -> TenantProfile:
    return tenant_profile(context)
