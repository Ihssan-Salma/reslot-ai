"""Privileged migration entry point; never run with the API's runtime credentials."""

import os
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from psycopg import sql
from sqlalchemy.engine import make_url

from app.core.config import get_settings


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    migration_url = os.environ.get("MIGRATION_DATABASE_URL")
    if migration_url:
        os.environ["DATABASE_URL"] = migration_url
        get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    password = os.environ.get("APP_DATABASE_PASSWORD")
    if not password:
        raise RuntimeError("Set APP_DATABASE_PASSWORD to provision the restricted runtime login")
    url = make_url(get_settings().database_url)
    with psycopg.connect(
        connect_timeout=5,
        host=url.host,
        port=url.port or 5432,
        dbname=url.database,
        user=url.username,
        password=url.password,
    ) as connection:
        if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname = 'reslot_app'").fetchone():
            connection.execute("CREATE ROLE reslot_app LOGIN")
        connection.execute(
            sql.SQL(
                "ALTER ROLE reslot_app WITH LOGIN INHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB "
                "NOCREATEROLE NOREPLICATION PASSWORD {}"
            ).format(sql.Literal(password))
        )
        connection.execute("GRANT reslot_runtime TO reslot_app")


if __name__ == "__main__":
    main()
