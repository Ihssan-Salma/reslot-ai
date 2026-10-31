# Retired reservation-management implementation

This source and its historical tests/documentation belong to the superseded booking-SaaS scope.
They are not imported, served, scheduled, built, or collected by the active application.
Existing database tables and migrations 0001?0008 are preserved; deleting/migrating historical
records requires a separate approved data-retention plan. Historical SQLAlchemy metadata is
kept only in backend/app/db/legacy_booking_metadata.py for safe Alembic drift checks.
Do not reactivate these endpoints or Calendar-write jobs as part of the automation foundation.
