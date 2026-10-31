"""A restricted worker claims database-owned outbox context; API roles cannot dispatch."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='reslot_outbox_executor') THEN
          CREATE ROLE reslot_outbox_executor NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
        END IF;
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='reslot_dispatcher') THEN
          CREATE ROLE reslot_dispatcher NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
        END IF;
      END $$;
      GRANT USAGE ON SCHEMA public TO reslot_outbox_executor,reslot_dispatcher;
      GRANT SELECT,UPDATE ON outbox_events TO reslot_outbox_executor;
      GRANT SELECT ON tenants,tenant_memberships TO reslot_outbox_executor;
      CREATE POLICY outbox_dispatch ON outbox_events TO reslot_outbox_executor USING (true) WITH CHECK (true);
      CREATE POLICY outbox_tenant_lookup ON tenants FOR SELECT TO reslot_outbox_executor USING (true);
      CREATE POLICY outbox_owner_lookup ON tenant_memberships FOR SELECT TO reslot_outbox_executor USING (true);
      CREATE FUNCTION public.claim_calendar_work(p_limit integer DEFAULT 10)
      RETURNS TABLE(event_id uuid,tenant_id uuid,user_id uuid,lease_token text)
      LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        WITH candidate AS (
          SELECT e.id,m.user_id FROM public.outbox_events e
          JOIN public.tenants t ON t.id=e.tenant_id AND t.status='active'
          JOIN LATERAL (SELECT member.user_id FROM public.tenant_memberships member
             WHERE member.tenant_id=e.tenant_id AND member.role IN ('owner','admin')
             ORDER BY (member.role='owner') DESC,member.id LIMIT 1) m ON true
          WHERE ((e.status IN ('PENDING','FAILED') AND e.next_attempt_at<=now() AND e.attempts<8)
                 OR (e.status='PROCESSING' AND e.lease_expires_at<now()))
          ORDER BY e.created_at,e.id LIMIT greatest(1,least(p_limit,50)) FOR UPDATE OF e SKIP LOCKED
        ) UPDATE public.outbox_events e SET status='PROCESSING',attempts=e.attempts+1,
            lease_token=gen_random_uuid()::text,lease_expires_at=now()+interval '10 minutes'
          FROM candidate c WHERE e.id=c.id RETURNING e.id,e.tenant_id,c.user_id,e.lease_token;
      $$;
      REVOKE ALL ON FUNCTION public.claim_calendar_work(integer) FROM PUBLIC;
      GRANT CREATE ON SCHEMA public TO reslot_outbox_executor;
      ALTER FUNCTION public.claim_calendar_work(integer) OWNER TO reslot_outbox_executor;
      REVOKE CREATE ON SCHEMA public FROM reslot_outbox_executor;
      GRANT EXECUTE ON FUNCTION public.claim_calendar_work(integer) TO reslot_dispatcher;
    """)


def downgrade() -> None:
    op.execute("""
      DROP FUNCTION public.claim_calendar_work(integer);
      DROP POLICY outbox_dispatch ON outbox_events;
      DROP POLICY outbox_tenant_lookup ON tenants;
      DROP POLICY outbox_owner_lookup ON tenant_memberships;
    """)
