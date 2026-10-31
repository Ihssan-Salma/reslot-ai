"""Authentication, tenant membership, and forced row-level security."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("business_type", sa.String(80), nullable=False, server_default="beauty_salon"),
    )
    op.add_column(
        "tenants",
        sa.Column("timezone", sa.String(100), nullable=False, server_default="Europe/Paris"),
    )
    op.add_column(
        "tenants", sa.Column("status", sa.String(20), nullable=False, server_default="active")
    )
    op.add_column(
        "tenants",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_check_constraint("tenant_status", "tenants", "status IN ('active', 'suspended')")
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "tenant_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.CheckConstraint("role IN ('owner', 'admin', 'staff')", name="membership_role"),
    )
    op.create_index("ix_tenant_memberships_tenant_id", "tenant_memberships", ["tenant_id"])
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("active_tenant_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["active_tenant_id"], ["tenants.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reslot_runtime') THEN
                CREATE ROLE reslot_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
            END IF;
        END $$;
        ALTER ROLE reslot_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
        REVOKE CREATE ON SCHEMA public FROM PUBLIC;
        GRANT USAGE ON SCHEMA public TO reslot_runtime;
        GRANT SELECT, INSERT, UPDATE ON users TO reslot_runtime;
        GRANT SELECT, INSERT, UPDATE, DELETE ON auth_sessions TO reslot_runtime;
        GRANT SELECT ON tenants, tenant_memberships TO reslot_runtime;

        ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
        ALTER TABLE tenant_memberships ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tenant_memberships FORCE ROW LEVEL SECURITY;

        CREATE POLICY membership_self ON tenant_memberships FOR SELECT TO reslot_runtime
        USING (
            user_id = nullif(current_setting('app.user_id', true), '')::uuid
            AND (nullif(current_setting('app.tenant_id', true), '') IS NULL
                 OR tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        );
        CREATE POLICY tenant_member ON tenants FOR SELECT TO reslot_runtime
        USING (
            (nullif(current_setting('app.tenant_id', true), '') IS NULL
             OR id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            AND EXISTS (
                SELECT 1 FROM public.tenant_memberships m
                WHERE m.tenant_id = tenants.id
                  AND m.user_id = nullif(current_setting('app.user_id', true), '')::uuid
            )
        );

        CREATE FUNCTION public.create_business(p_name text, p_type text, p_timezone text)
        RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
            v_user uuid := nullif(current_setting('app.user_id', true), '')::uuid;
            v_tenant uuid := gen_random_uuid();
        BEGIN
            IF v_user IS NULL THEN
                RAISE EXCEPTION 'Authenticated context required' USING ERRCODE = '42501';
            END IF;
            IF length(trim(p_name)) < 1 OR length(trim(p_type)) < 1 THEN
                RAISE EXCEPTION 'Invalid business details' USING ERRCODE = '23514';
            END IF;
            INSERT INTO public.tenants(id, name, business_type, timezone, status)
                VALUES (v_tenant, p_name, p_type, p_timezone, 'active');
            INSERT INTO public.tenant_memberships(id, user_id, tenant_id, role)
                VALUES (gen_random_uuid(), v_user, v_tenant, 'owner');
            RETURN v_tenant;
        END $$;
        REVOKE ALL ON FUNCTION public.create_business(text, text, text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.create_business(text, text, text) TO reslot_runtime;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION public.create_business(text, text, text);
        DROP POLICY tenant_member ON tenants;
        DROP POLICY membership_self ON tenant_memberships;
        ALTER TABLE tenants NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE tenants DISABLE ROW LEVEL SECURITY;
        REVOKE SELECT ON tenants FROM reslot_runtime;
    """)
    op.drop_table("auth_sessions")
    op.drop_table("tenant_memberships")
    op.drop_table("users")
    op.drop_constraint("ck_tenants_tenant_status", "tenants", type_="check")
    for column in ("created_at", "status", "timezone", "business_type"):
        op.drop_column("tenants", column)
    # Keep the shared role and schema hardening; they may be used by other databases.
