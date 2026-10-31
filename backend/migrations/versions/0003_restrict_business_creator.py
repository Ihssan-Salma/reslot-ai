"""Make the narrow bootstrap function obey RLS under a non-login role."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reslot_creator') THEN
                CREATE ROLE reslot_creator NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
            END IF;
        END $$;
        ALTER ROLE reslot_creator NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
        GRANT USAGE ON SCHEMA public TO reslot_creator;
        GRANT INSERT ON tenants, tenant_memberships TO reslot_creator;
        CREATE POLICY tenant_bootstrap ON tenants FOR INSERT TO reslot_creator
        WITH CHECK (
            nullif(current_setting('app.user_id', true), '') IS NOT NULL AND status = 'active'
        );
        CREATE POLICY owner_bootstrap ON tenant_memberships FOR INSERT TO reslot_creator
        WITH CHECK (
            user_id = nullif(current_setting('app.user_id', true), '')::uuid AND role = 'owner'
        );
        GRANT CREATE ON SCHEMA public TO reslot_creator;
        ALTER FUNCTION public.create_business(text, text, text) OWNER TO reslot_creator;
        REVOKE CREATE ON SCHEMA public FROM reslot_creator;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER FUNCTION public.create_business(text, text, text) OWNER TO CURRENT_USER;
        DROP POLICY owner_bootstrap ON tenant_memberships;
        DROP POLICY tenant_bootstrap ON tenants;
        REVOKE INSERT ON tenants, tenant_memberships FROM reslot_creator;
    """)
