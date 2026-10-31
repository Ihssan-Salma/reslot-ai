import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.db.session import get_engine
from app.main import create_app


@pytest.fixture(scope="session")
def database() -> Iterator[tuple[Engine, Engine]]:
    admin_url = os.getenv("TEST_ADMIN_DATABASE_URL")
    runtime_url = os.getenv("TEST_DATABASE_URL")
    if not admin_url or not runtime_url:
        pytest.skip("Set TEST_ADMIN_DATABASE_URL and TEST_DATABASE_URL for real PostgreSQL tests")
    # Always create a uniquely named disposable database; never truncate application data.
    name = "reslot_test_" + uuid4().hex
    admin = create_engine(
        admin_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5}
    )
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    owner_url = make_url(admin_url).set(database=name)
    app_url = make_url(runtime_url).set(database=name)
    owner = create_engine(owner_url, connect_args={"connect_timeout": 5})
    runtime = create_engine(
        app_url, connect_args={"connect_timeout": 5}, pool_size=1, max_overflow=0
    )
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("DATABASE_URL", owner_url.render_as_string(hide_password=False))
            get_settings.cache_clear()
            command.upgrade(Config("alembic.ini"), "head")
            command.check(Config("alembic.ini"))
            patch.setenv("DATABASE_URL", app_url.render_as_string(hide_password=False))
            patch.setenv("COOKIE_SECURE", "false")
            patch.setenv("APP_ENV", "development")
            get_settings.cache_clear()
            get_engine.cache_clear()
            yield runtime, owner
            get_engine().dispose()
            get_engine.cache_clear()
    finally:
        runtime.dispose()
        owner.dispose()
        get_settings.cache_clear()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def client(database: tuple[Engine, Engine]) -> Iterator[TestClient]:
    with TestClient(create_app()) as result:
        yield result
