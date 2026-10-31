"""Derive allocation occupancy and quantity from the reservation, not caller flags."""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "reservation_date_pair",
        "reservations",
        "(local_start_date IS NULL) = (local_end_date IS NULL)",
    )
    op.execute("""
      ALTER ROLE reslot_booking_guard NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
      ALTER ROLE reslot_outbox_executor NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
      ALTER ROLE reslot_dispatcher NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

      CREATE OR REPLACE FUNCTION public.reslot_allocation_guard() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
      DECLARE r public.resources; b public.reservations; gap integer;
      BEGIN
        UPDATE public.resources SET inventory_version=inventory_version+1 WHERE tenant_id=NEW.tenant_id AND id=NEW.resource_id RETURNING * INTO r;
        SELECT * INTO b FROM public.reservations WHERE tenant_id=NEW.tenant_id AND id=NEW.reservation_id;
        IF r.id IS NULL OR b.id IS NULL THEN RAISE EXCEPTION 'Authorized resource and reservation required' USING ERRCODE='42501'; END IF;
        IF NOT EXISTS (SELECT FROM public.offering_resources WHERE tenant_id=NEW.tenant_id AND offering_id=b.offering_id AND resource_id=r.id) THEN
          RAISE EXCEPTION 'Incompatible resource' USING ERRCODE='23514';
        END IF;
        IF b.party_size IS NOT NULL AND r.capacity IS NOT NULL AND b.party_size>r.capacity THEN
          RAISE EXCEPTION 'Resource party capacity exceeded' USING ERRCODE='23514';
        END IF;
        SELECT turnaround_minutes INTO gap FROM public.booking_policies WHERE tenant_id=NEW.tenant_id AND offering_id=b.offering_id;
        NEW.start_at=b.start_at; NEW.end_at=b.end_at;
        NEW.occupied_until=b.end_at+make_interval(mins=>coalesce(gap,0));
        NEW.exclusive=(r.allocation_mode='EXCLUSIVE');
        NEW.allocation_status=CASE WHEN b.status IN ('PENDING','CONFIRMED') THEN 'ACTIVE' ELSE 'RELEASED' END;
        NEW.allocated_quantity=CASE WHEN NEW.exclusive THEN 1 ELSE b.quantity END;
        RETURN NEW;
      END $$;
      CREATE OR REPLACE FUNCTION public.reslot_reservation_guard() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
      DECLARE cap integer; peak integer;
      BEGIN
        UPDATE public.offerings SET inventory_version=inventory_version+1 WHERE id=NEW.offering_id AND tenant_id=NEW.tenant_id RETURNING capacity_limit INTO cap;
        IF NOT FOUND THEN RAISE EXCEPTION 'Authorized offering required' USING ERRCODE='42501'; END IF;
        IF cap IS NOT NULL AND NEW.status IN ('PENDING','CONFIRMED') THEN
          SELECT max(usage) INTO peak FROM (
            SELECT sum(delta) OVER (ORDER BY instant) AS usage FROM (
              SELECT instant,sum(delta) delta FROM (
                SELECT start_at instant,quantity delta FROM public.reservations WHERE tenant_id=NEW.tenant_id AND offering_id=NEW.offering_id AND status IN ('PENDING','CONFIRMED')
                UNION ALL SELECT end_at,-quantity FROM public.reservations WHERE tenant_id=NEW.tenant_id AND offering_id=NEW.offering_id AND status IN ('PENDING','CONFIRMED')
              ) changes GROUP BY instant
            ) grouped
          ) totals;
          IF peak>cap THEN RAISE EXCEPTION 'Offering capacity exceeded' USING ERRCODE='23514'; END IF;
        END IF;
        IF TG_OP='UPDATE' AND (NEW.status!=OLD.status OR NEW.quantity!=OLD.quantity OR NEW.offering_id!=OLD.offering_id) THEN
          UPDATE public.reservation_resources SET allocation_status=CASE WHEN NEW.status IN ('PENDING','CONFIRMED') THEN 'ACTIVE' ELSE 'RELEASED' END, allocated_quantity=CASE WHEN exclusive THEN 1 ELSE NEW.quantity END
            WHERE tenant_id=NEW.tenant_id AND reservation_id=NEW.id;
        END IF;
        RETURN NEW;
      END $$;
    """)


def downgrade() -> None:
    op.drop_constraint("ck_reservations_reservation_date_pair", "reservations", type_="check")
    # The stronger trigger definitions remain compatible with revision 0007's schema.
