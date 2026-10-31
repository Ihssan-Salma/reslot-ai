import pytest
from sqlalchemy import Engine, inspect, text


@pytest.mark.integration
def test_postgres_and_migrated_schema(database: tuple[Engine, Engine]) -> None:
    runtime, owner = database
    with runtime.connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1
        assert {
            "users",
            "tenants",
            "tenant_memberships",
            "auth_sessions",
            "google_connections",
            "business_calendars",
            "google_oauth_states",
        } <= set(inspect(connection).get_table_names())
    with owner.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0009"
