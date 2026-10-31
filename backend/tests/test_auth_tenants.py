from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.security import digest_token
from app.db.session import assert_runtime_role, set_tenant_context, set_user_context
from app.main import create_app
from app.models import AuthSession, Tenant, TenantMembership, User

pytestmark = pytest.mark.integration
ORIGIN = "http://localhost:5173"
PASSWORD = "A long unique test passphrase 2026!"


def post(client: TestClient, path: str, data: dict[str, object]) -> Response:
    token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    return client.post(
        "/api/v1" + path, json=data, headers={"Origin": ORIGIN, "X-CSRF-Token": token}
    )


def register_login(client: TestClient) -> dict[str, object]:
    data = {"email": uuid4().hex + "@example.com", "password": PASSWORD}
    response = post(client, "/auth/register", data)
    assert response.status_code == 201
    assert "password_hash" not in response.json()
    assert post(client, "/auth/login", data).status_code == 200
    return data


@pytest.fixture
def account(client: TestClient) -> tuple[TestClient, dict[str, object]]:
    return client, register_login(client)


@pytest.fixture
def salons(database: tuple[Engine, Engine]) -> Iterator[tuple[TestClient, TestClient, dict, dict]]:
    with TestClient(create_app()) as a, TestClient(create_app()) as b:
        register_login(a)
        register_login(b)
        salon_a = post(a, "/tenants", {"name": "Salon A"})
        salon_b = post(b, "/tenants", {"name": "Salon B"})
        assert salon_a.status_code == salon_b.status_code == 201
        yield a, b, salon_a.json(), salon_b.json()


def test_registration_login_profile_and_password_storage(account, database) -> None:
    client, credentials = account
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == credentials["email"]
    _, owner = database
    with Session(owner) as db:
        user = db.scalar(select(User).where(User.email == credentials["email"]))
        assert user.password_hash.startswith("$argon2id$")
        assert credentials["password"] not in user.password_hash
        assert user.updated_at >= user.created_at
        raw = client.cookies["reslot_session"]
        assert db.scalar(
            select(AuthSession.token_hash).where(AuthSession.user_id == user.id)
        ) == digest_token(raw)


def test_duplicate_registration_normalizes_email(account) -> None:
    client, data = account
    assert (
        post(client, "/auth/register", {**data, "email": data["email"].upper()}).status_code == 409
    )


def test_weak_password_is_rejected(client) -> None:
    assert (
        post(
            client, "/auth/register", {"email": "valid@example.com", "password": "short"}
        ).status_code
        == 422
    )


def test_incorrect_credentials(account) -> None:
    client, data = account
    for credentials in (
        {**data, "password": "An incorrect passphrase"},
        {**data, "password": "short"},
        {**data, "email": "absent@example.com"},
    ):
        response = post(client, "/auth/login", credentials)
        assert response.status_code == 401
        assert response.json() == {"detail": "Incorrect email or password"}


@pytest.mark.parametrize("path", ["/auth/me", "/tenants", "/dashboard"])
def test_unauthorized_api_access(client, path) -> None:
    assert client.get("/api/v1" + path).status_code == 401


def test_tenant_creation_requires_authentication(client) -> None:
    assert post(client, "/tenants", {"name": "Unauthenticated"}).status_code == 401


def test_two_businesses_have_isolated_dashboards_and_ownership(salons) -> None:
    a, b, salon_a, salon_b = salons
    for own, other, mine, theirs in ((a, b, salon_a, salon_b), (b, a, salon_b, salon_a)):
        response = own.get("/api/v1/dashboard")
        assert response.status_code == 200
        assert response.json()["name"] == mine["name"]
        assert response.json()["role"] == "owner"
        assert own.get(f"/api/v1/tenants/{theirs['id']}/dashboard").status_code == 404
        assert other.get(f"/api/v1/tenants/{mine['id']}/dashboard").status_code == 404
        assert [item["id"] for item in own.get("/api/v1/tenants").json()] == [mine["id"]]


def test_invalid_switch_does_not_change_selected_tenant(salons) -> None:
    a, _, salon_a, salon_b = salons
    for tenant_id in (salon_b["id"], str(uuid4())):
        assert post(a, "/tenants/switch", {"tenant_id": tenant_id}).status_code == 404
        assert a.get("/api/v1/dashboard").json()["id"] == salon_a["id"]


def test_owner_can_switch_multiple_businesses(account) -> None:
    client, _ = account
    one = post(client, "/tenants", {"name": "First"}).json()
    two = post(client, "/tenants", {"name": "Second"}).json()
    assert len(client.get("/api/v1/tenants").json()) == 2
    assert post(client, "/tenants/switch", {"tenant_id": one["id"]}).status_code == 200
    assert client.get("/api/v1/dashboard").json()["id"] == one["id"]
    assert post(client, "/tenants/switch", {"tenant_id": two["id"]}).status_code == 200
    assert client.get("/api/v1/auth/me").json()["active_tenant_id"] == two["id"]


@pytest.mark.parametrize("role", ["admin", "staff"])
def test_authorized_member_can_switch_to_a_business(salons, database, role) -> None:
    a, b, salon_a, _ = salons
    user_b = UUID(b.get("/api/v1/auth/me").json()["id"])
    with Session(database[1]) as db, db.begin():
        db.add(TenantMembership(user_id=user_b, tenant_id=UUID(salon_a["id"]), role=role))
    response = post(b, "/tenants/switch", {"tenant_id": salon_a["id"]})
    assert response.status_code == 200
    assert response.json()["role"] == role
    assert b.get("/api/v1/dashboard").json()["name"] == "Salon A"
    assert a.get("/api/v1/dashboard").json()["role"] == "owner"


def test_revoked_membership_is_rechecked(salons, database) -> None:
    a, _, salon_a, _ = salons
    user_id = UUID(a.get("/api/v1/auth/me").json()["id"])
    with database[1].begin() as connection:
        connection.execute(
            text("DELETE FROM tenant_memberships WHERE user_id=:user"), {"user": user_id}
        )
    assert a.get("/api/v1/dashboard").status_code == 404
    assert a.get("/api/v1/tenants").json() == []


def test_suspended_tenant_is_denied(salons, database) -> None:
    a, _, salon_a, _ = salons
    with database[1].begin() as connection:
        connection.execute(
            update(Tenant).where(Tenant.id == UUID(salon_a["id"])).values(status="suspended")
        )
    assert a.get("/api/v1/dashboard").status_code == 403
    assert post(a, "/tenants/switch", {"tenant_id": salon_a["id"]}).status_code == 403


def test_csrf_rejection_and_session_binding(account) -> None:
    client, credentials = account
    assert (
        client.post(
            "/api/v1/tenants", json={"name": "Denied"}, headers={"Origin": ORIGIN}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/auth/logout", json={}, headers={"Origin": ORIGIN, "X-CSRF-Token": "forged"}
        ).status_code
        == 403
    )
    csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    for origin in ("https://evil.example", "null", ""):
        assert (
            client.post(
                "/api/v1/auth/logout", json={}, headers={"Origin": origin, "X-CSRF-Token": csrf}
            ).status_code
            == 403
        )
    assert post(client, "/auth/login", credentials).status_code == 200
    assert (
        client.post(
            "/api/v1/auth/logout", json={}, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
        ).status_code
        == 403
    )


def test_login_and_registration_require_csrf(client) -> None:
    data = {"email": "someone@example.com", "password": PASSWORD}
    for path in ("login", "register"):
        assert (
            client.post(f"/api/v1/auth/{path}", json=data, headers={"Origin": ORIGIN}).status_code
            == 403
        )


def test_cookie_security_and_session_rotation(account) -> None:
    client, credentials = account
    old_cookie = client.cookies["reslot_session"]
    response = post(client, "/auth/login", credentials)
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header and "path=/" in header
    assert "reslot_session" not in response.json()
    assert client.cookies["reslot_session"] != old_cookie
    with TestClient(create_app()) as other:
        other.cookies.set("reslot_session", old_cookie)
        assert other.get("/api/v1/auth/me").status_code == 401


def test_logout_revokes_server_side_session(account) -> None:
    client, _ = account
    token = client.cookies["reslot_session"]
    assert post(client, "/auth/logout", {}).status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.set("reslot_session", token)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_expired_session_is_denied(account, database) -> None:
    client, _ = account
    with database[1].begin() as connection:
        connection.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == digest_token(client.cookies["reslot_session"]))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert client.get("/api/v1/auth/me").status_code == 401


def test_client_cannot_supply_role_or_user_context(account) -> None:
    client, _ = account
    for extra in ({"user_id": str(uuid4())}, {"role": "owner"}, {"tenant_id": str(uuid4())}):
        assert post(client, "/tenants", {"name": "Forbidden", **extra}).status_code == 422
    assert (
        post(client, "/tenants", {"name": "Bad zone", "timezone": "invalid/zone"}).status_code
        == 422
    )


def test_rls_blocks_missing_and_forged_tenant_context(salons, database) -> None:
    a, b, salon_a, salon_b = salons
    runtime, _ = database
    for client, mine, theirs in ((a, salon_a, salon_b), (b, salon_b, salon_a)):
        user_id = UUID(client.get("/api/v1/auth/me").json()["id"])
        with Session(runtime) as db, db.begin():
            assert db.scalars(select(Tenant)).all() == []
            assert db.scalars(select(TenantMembership)).all() == []
            set_user_context(db, user_id)
            set_tenant_context(db, UUID(mine["id"]))
            assert [row.name for row in db.scalars(select(Tenant))] == [mine["name"]]
            assert len(db.scalars(select(TenantMembership)).all()) == 1
            # Even a direct forged tenant GUC cannot override membership-based RLS.
            set_tenant_context(db, UUID(theirs["id"]))
            assert db.execute(text("SELECT * FROM tenants")).all() == []
            assert db.execute(text("SELECT * FROM tenant_memberships")).all() == []
        with runtime.connect() as connection:
            assert (
                connection.scalar(text("SELECT nullif(current_setting('app.user_id',true),'')"))
                is None
            )
            assert (
                connection.scalar(text("SELECT nullif(current_setting('app.tenant_id',true),'')"))
                is None
            )
            assert connection.execute(text("SELECT * FROM tenants")).all() == []


def test_runtime_role_cannot_bypass_rls_or_write_memberships(salons, database) -> None:
    runtime, owner = database
    assert_runtime_role(runtime)
    with pytest.raises(RuntimeError, match="restricted"):
        assert_runtime_role(owner)
    statements = [
        "SET ROLE reslot",
        "ALTER TABLE tenants DISABLE ROW LEVEL SECURITY",
        "TRUNCATE tenant_memberships",
        "INSERT INTO tenant_memberships VALUES "
        "(gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'owner')",
        "UPDATE tenants SET name='Compromised'",
    ]
    for statement in statements:
        with runtime.connect() as connection, pytest.raises(DBAPIError):
            connection.execute(text(statement))
    with runtime.connect() as connection, pytest.raises(DBAPIError):
        connection.execute(text("SET LOCAL row_security = off"))
        connection.execute(text("SELECT * FROM tenants"))
    with runtime.connect() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text("SELECT create_business('Unauthenticated','beauty_salon','Europe/Paris')")
        )


def test_forged_client_context_is_ignored(salons) -> None:
    a, _, mine, theirs = salons
    response = a.get(
        "/api/v1/dashboard",
        params={"tenant_id": theirs["id"]},
        headers={"X-Tenant-ID": theirs["id"], "X-User-ID": str(uuid4())},
    )
    assert response.status_code == 200
    assert response.json()["id"] == mine["id"]


def test_database_tampered_selection_still_requires_membership(salons, database) -> None:
    a, _, _, other = salons
    with database[1].begin() as connection:
        connection.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == digest_token(a.cookies["reslot_session"]))
            .values(active_tenant_id=UUID(other["id"]))
        )
    assert a.get("/api/v1/dashboard").status_code == 404


def test_creator_function_has_no_rls_bypass(database) -> None:
    with database[1].connect() as connection:
        row = connection.execute(
            text("""
            SELECT r.rolsuper, r.rolbypassrls, r.rolcanlogin
            FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname='public' AND p.proname='create_business'
        """)
        ).one()
        assert tuple(row) == (False, False, False)
    with database[0].connect() as connection, pytest.raises(DBAPIError):
        connection.execute(text("SET ROLE reslot_creator"))


def test_rolled_back_context_does_not_leak(salons, database) -> None:
    a, _, mine, _ = salons
    runtime, _ = database
    user_id = UUID(a.get("/api/v1/auth/me").json()["id"])
    with pytest.raises(ValueError, match="Rollback"):
        with Session(runtime) as db, db.begin():
            set_user_context(db, user_id)
            set_tenant_context(db, UUID(mine["id"]))
            assert db.scalar(select(Tenant.id)) == UUID(mine["id"])
            raise ValueError("Rollback")
    with Session(runtime) as db:
        assert db.scalars(select(Tenant)).all() == []
        assert db.scalars(select(TenantMembership)).all() == []
