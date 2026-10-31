"""Tenant Google connections, encrypted tokens, OAuth attempts, and calendar selection."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "google_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("google_subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("granted_scopes", postgresql.JSONB(), nullable=False),
        sa.Column("connection_status", sa.String(30), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_google_connections_tenant_connection"),
        sa.UniqueConstraint(
            "tenant_id", "google_subject", name="uq_google_connections_tenant_subject"
        ),
        sa.CheckConstraint(
            "connection_status IN ('connected', 'reconnect_required', "
            "'disconnect_pending', 'disconnected')",
            name="google_connection_status",
        ),
    )
    op.create_table(
        "business_calendars",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("google_connection_id", sa.Uuid(), nullable=False),
        sa.Column("google_calendar_id", sa.String(1024), nullable=False),
        sa.Column("calendar_name", sa.String(1024), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("access_role", sa.String(30), nullable=False),
        sa.Column("is_selected", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "google_connection_id"],
            ["google_connections.tenant_id", "google_connections.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "google_connection_id", "google_calendar_id"),
        sa.CheckConstraint(
            "NOT is_selected OR access_role IN ('owner', 'writer')",
            name="selected_calendar_writable",
        ),
    )
    op.create_index(
        "ix_business_calendars_one_selected",
        "business_calendars",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_selected"),
    )
    op.create_table(
        "google_oauth_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("nonce_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_code_verifier", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["auth_sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_google_oauth_states_tenant_id", "google_oauth_states", ["tenant_id"])
    for table in ("google_connections", "business_calendars", "google_oauth_states"):
        # Identifiers are fixed migration constants, never user input.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO reslot_runtime")
        predicate = f"""
            tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
            AND EXISTS (
                SELECT 1 FROM public.tenant_memberships m
                WHERE m.tenant_id = {table}.tenant_id
                  AND m.user_id = nullif(current_setting('app.user_id', true), '')::uuid
                  AND m.role IN ('owner', 'admin')
            )
        """
        op.execute(
            f"CREATE POLICY tenant_google_manager ON {table} TO reslot_runtime "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )


def downgrade() -> None:
    op.drop_table("google_oauth_states")
    op.drop_table("business_calendars")
    op.drop_table("google_connections")
