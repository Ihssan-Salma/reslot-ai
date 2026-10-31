from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.db import legacy_booking_metadata  # noqa: F401
from app.db.base import Base
from app.models import (  # noqa: F401
    AuthSession,
    BusinessCalendar,
    GoogleConnection,
    GoogleOAuthState,
    Tenant,
    TenantMembership,
    User,
)

target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(
        get_settings().database_url, poolclass=pool.NullPool, connect_args={"connect_timeout": 5}
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
