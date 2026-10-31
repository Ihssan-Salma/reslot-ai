# Task 04 source changes

Created:

- `backend/app/api/routes/booking.py`
- `backend/app/core/domain.py`
- `backend/app/integrations/google/calendar_writer.py`
- `backend/app/models/booking.py`
- `backend/app/repositories/booking.py`
- `backend/app/schemas/booking.py`
- `backend/app/services/allocation.py`
- `backend/app/services/availability.py`
- `backend/app/services/booking_policies.py`
- `backend/app/services/calendar_payload.py`
- `backend/app/services/calendar_sync.py`
- `backend/app/services/catalog.py`
- `backend/app/services/periods.py`
- `backend/app/services/reservations.py`
- `backend/app/services/waitlist.py`
- `backend/app/tasks/__init__.py`
- `backend/app/tasks/calendar_sync.py`
- `backend/migrations/versions/0005_generic_reservations_and_inventory.py`
- `backend/migrations/versions/0006_outbox_dispatcher.py`
- `backend/migrations/versions/0007_inventory_configuration_guards.py`
- `backend/migrations/versions/0008_allocation_integrity.py`
- `backend/tests/test_booking.py`
- `backend/tests/test_calendar_sync.py`
- `docs/reservation-api.md`
- `docs/reservation-manual-test.md`
- `docs/reservations.md`
- `docs/task04-files.md`
- `frontend/src/booking/BookingWorkspace.tsx`
- `frontend/src/booking/CatalogPanels.tsx`
- `frontend/src/booking/OtherPanels.tsx`
- `frontend/src/booking/ReservationPanels.tsx`
- `frontend/src/booking/shared.tsx`

Modified:

- `.env.example`
- `.github/workflows/ci.yml`
- `README.md`
- `backend/app/core/celery_app.py`
- `backend/app/db/migrate.py`
- `backend/app/db/session.py`
- `backend/app/main.py`
- `backend/app/models/__init__.py`
- `backend/app/schemas/auth.py`
- `backend/pyproject.toml`
- `backend/scripts/test_backend.py`
- `backend/tests/test_database.py`
- `compose.yaml`
- `docs/architecture.md`
- `docs/authentication.md`
- `docs/files.md`
- `docs/google-security.md`
- `docs/verification.md`
- `frontend/src/App.tsx`
- `frontend/src/GoogleIntegrations.tsx`
- `frontend/src/Landing.tsx`
- `frontend/src/api.ts`

No source files were deleted. Migrations 0001?0004 were preserved. The ignored local `.env`
was extended with generated worker credentials; generated caches, build logs, dependencies,
and build output are excluded. User IDE settings were left untouched.
