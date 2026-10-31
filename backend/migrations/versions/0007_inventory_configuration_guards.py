"""Protect live inventory configuration and allow worker-scoped single-item recovery."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE FUNCTION public.claim_calendar_item(p_event uuid)
      RETURNS TABLE(event_id uuid,tenant_id uuid,user_id uuid,lease_token text)
      LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        WITH candidate AS (
          SELECT e.id,m.user_id FROM public.outbox_events e
          JOIN public.tenants t ON t.id=e.tenant_id AND t.status='active'
          JOIN LATERAL (SELECT member.user_id FROM public.tenant_memberships member
             WHERE member.tenant_id=e.tenant_id AND member.role IN ('owner','admin')
             ORDER BY (member.role='owner') DESC,member.id LIMIT 1) m ON true
          WHERE e.id=p_event AND ((e.status IN ('PENDING','FAILED') AND e.next_attempt_at<=now() AND e.attempts<8)
                 OR (e.status='PROCESSING' AND e.lease_expires_at<now()))
          ORDER BY e.created_at,e.id LIMIT 1 FOR UPDATE OF e SKIP LOCKED
        ) UPDATE public.outbox_events e SET status='PROCESSING',attempts=e.attempts+1,
            lease_token=gen_random_uuid()::text,lease_expires_at=now()+interval '10 minutes'
          FROM candidate c WHERE e.id=c.id RETURNING e.id,e.tenant_id,c.user_id,e.lease_token;
      $$;

      REVOKE ALL ON FUNCTION public.claim_calendar_item(uuid) FROM PUBLIC;
      GRANT CREATE ON SCHEMA public TO reslot_outbox_executor;
      ALTER FUNCTION public.claim_calendar_item(uuid) OWNER TO reslot_outbox_executor;
      REVOKE CREATE ON SCHEMA public FROM reslot_outbox_executor;
      GRANT EXECUTE ON FUNCTION public.claim_calendar_item(uuid) TO reslot_dispatcher;
      CREATE FUNCTION public.reslot_inventory_configuration_guard() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$ BEGIN
        IF TG_TABLE_NAME='resources' THEN
          IF (NEW.capacity IS DISTINCT FROM OLD.capacity OR NEW.allocation_mode!=OLD.allocation_mode OR NEW.resource_type!=OLD.resource_type)
             AND EXISTS(SELECT FROM public.reservation_resources WHERE tenant_id=NEW.tenant_id AND resource_id=NEW.id AND allocation_status='ACTIVE') THEN
            RAISE EXCEPTION 'Release active allocations before changing resource rules' USING ERRCODE='23514';
          END IF;
        ELSE
          IF (NEW.capacity_limit IS DISTINCT FROM OLD.capacity_limit OR NEW.booking_mode!=OLD.booking_mode
              OR NEW.min_duration_minutes IS DISTINCT FROM OLD.min_duration_minutes OR NEW.max_duration_minutes IS DISTINCT FROM OLD.max_duration_minutes
              OR NEW.default_duration_minutes IS DISTINCT FROM OLD.default_duration_minutes)
             AND EXISTS(SELECT FROM public.reservations WHERE tenant_id=NEW.tenant_id AND offering_id=NEW.id AND status IN ('PENDING','CONFIRMED')) THEN
            RAISE EXCEPTION 'Finish active reservations before changing offering rules' USING ERRCODE='23514';
          END IF;
        END IF;
        RETURN NEW;
      END $$;
      REVOKE ALL ON FUNCTION public.reslot_inventory_configuration_guard() FROM PUBLIC;
      CREATE TRIGGER resource_configuration BEFORE UPDATE ON resources FOR EACH ROW EXECUTE FUNCTION public.reslot_inventory_configuration_guard();
      CREATE TRIGGER offering_configuration BEFORE UPDATE ON offerings FOR EACH ROW EXECUTE FUNCTION public.reslot_inventory_configuration_guard();
    """)


def downgrade() -> None:
    op.execute("""
      DROP TRIGGER resource_configuration ON resources;
      DROP TRIGGER offering_configuration ON offerings;
      DROP FUNCTION public.reslot_inventory_configuration_guard();
      DROP FUNCTION public.claim_calendar_item(uuid);
    """)
