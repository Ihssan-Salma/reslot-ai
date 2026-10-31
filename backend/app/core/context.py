from dataclasses import dataclass

from app.models import AuthSession, Tenant, TenantMembership, User


@dataclass
class AuthContext:
    user: User
    login: AuthSession


@dataclass
class TenantContext:
    tenant: Tenant
    membership: TenantMembership
