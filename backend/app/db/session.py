from collections.abc import Iterator
from functools import lru_cache
from uuid import UUID

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url, pool_pre_ping=True, connect_args={"connect_timeout": 5}
    )


def assert_runtime_role(engine: Engine) -> None:
    with engine.connect() as connection:
        unsafe = connection.scalar(
            text("""
            SELECT bool_or(rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb)
            FROM pg_roles WHERE pg_has_role(current_user, oid, 'MEMBER')
        """)
        )
        if unsafe is not False:
            raise RuntimeError("The API requires a restricted PostgreSQL runtime role")
        protected = connection.scalar(
            text("""
            SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname IN (
                  'tenants', 'tenant_memberships', 'google_connections',
                  'business_calendars', 'google_oauth_states', 'spreadsheet_configurations',
                  'calendar_event_snapshots','integration_cursors','workflow_states',
                  'integration_idempotency'
              )
              AND c.relrowsecurity AND c.relforcerowsecurity
              AND NOT pg_has_role(current_user, c.relowner, 'MEMBER')
        """)
        )
        if protected != 10:
            raise RuntimeError("Tenant tables must have forced RLS and a separate owner")


def set_user_context(session: Session, user_id: UUID) -> None:
    session.execute(text("SELECT set_config('app.user_id', :value, true)"), {"value": str(user_id)})


def set_tenant_context(session: Session, tenant_id: UUID | None) -> None:
    session.execute(
        text("SELECT set_config('app.tenant_id', :value, true)"),
        {"value": str(tenant_id) if tenant_id else ""},
    )


def get_session() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as session, session.begin():
        # Explicitly clear context at every boundary, in addition to SET LOCAL expiry.
        session.execute(text("SELECT set_config('app.user_id', '', true)"))
        set_tenant_context(session, None)
        yield session
