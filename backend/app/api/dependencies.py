from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.context import AuthContext as AuthContext
from app.core.context import TenantContext as TenantContext
from app.core.security import digest_token, session_cookie_name
from app.db.session import get_session, set_tenant_context, set_user_context
from app.models import User
from app.repositories.auth import AuthRepository
from app.repositories.tenants import TenantRepository

Db = Annotated[Session, Depends(get_session, scope="function")]


def authenticated(request: Request, db: Db) -> AuthContext:
    token = request.cookies.get(session_cookie_name(), "")
    login = AuthRepository(db).valid_session(digest_token(token)) if token else None
    if login is None:
        raise HTTPException(401, "Authentication required")
    user = db.get(User, login.user_id)
    if user is None:
        raise HTTPException(401, "Authentication required")
    set_user_context(db, user.id)
    return AuthContext(user, login)


CurrentUser = Annotated[AuthContext, Depends(authenticated)]


def authorize_tenant(db: Session, auth: AuthContext, tenant_id: object) -> TenantContext:
    from uuid import UUID

    if not isinstance(tenant_id, UUID):
        raise HTTPException(409, "Select a business first")
    repository = TenantRepository(db, auth.user.id)
    membership = repository.membership(tenant_id)
    if membership is None:
        raise HTTPException(404, "Business not found")
    set_tenant_context(db, membership.tenant_id)
    tenant = repository.tenant(membership.tenant_id)
    if tenant is None:
        raise HTTPException(404, "Business not found")
    if tenant.status != "active":
        raise HTTPException(403, "Business is suspended")
    return TenantContext(tenant, membership)


def selected_tenant(auth: CurrentUser, db: Db) -> TenantContext:
    return authorize_tenant(db, auth, auth.login.active_tenant_id)


CurrentTenant = Annotated[TenantContext, Depends(selected_tenant)]
