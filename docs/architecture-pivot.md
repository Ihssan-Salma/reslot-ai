# Automation architecture and retained history

ReSlot AI coordinates cancellation recovery using existing business systems. Calendar owns
reservations; Gmail owns messages; an authorized Google Sheet owns customers and waiting lists.
It is not a booking management system. LangGraph agents and MCP tools are future work.

The HTTP boundary authenticates the session, verifies tenant membership, derives transaction-local
tenant context, and checks owner/admin permission before calling integration services. Repositories
apply explicit tenant predicates in addition to forced PostgreSQL RLS. Runtime roles cannot own
tenant tables or bypass RLS. The Google service serializes integration operations per tenant with
a transaction advisory lock, handles refresh/revocation, and never returns credentials to clients.

`GoogleSheetsService` validates the configured external dataset and offers narrow read/lookup/status
operations. `CalendarReservationService` reads the selected calendar, normalizes marked events,
and checks busy intervals. They have no dependency on HTTP handlers and can be adapted to future
authenticated MCP tools. Future tools must receive an already authorized server-side tenant
context; an LLM-supplied tenant or spreadsheet ID must never set database context.

## Persistence boundary

Migration `0009` adds only:

| Table | Purpose |
| --- | --- |
| spreadsheet_configurations | One authorized file and Google account binding per business |
| calendar_event_snapshots | Latest minimal normalized metadata/classification for explicitly read events |
| integration_cursors | Future encrypted provider cursors; not populated yet |
| workflow_states | Future workflow checkpoints; no workflows run in this task |
| integration_idempotency | Tenant-scoped status-write receipts, no source customer rows |

Existing auth, membership, OAuth state, Google connection, and calendar configuration remain.
There are no new customer, waiting-list, reservation, service, employee, room, or table databases.
Snapshots deliberately omit event descriptions, summaries, attendees and email bodies. Normalized
customer references and requirements may still be personal data: apply deployment retention and
access policies. No background synchronization, Gmail monitoring, agents, or Calendar writes run.

## Obsolete prior Task 04 implementation

The previous reservation CRUD, catalog, allocation, customer management, booking policies,
wait-list CRUD, Calendar writer/outbox dispatcher, tests and UI are archived under
`legacy/reservation_management/`. They are excluded from backend imports, image build context,
frontend build and pytest discovery. Only historical SQLAlchemy declarations remain in
`app/db/legacy_booking_metadata.py`, imported by Alembic to avoid proposing accidental table drops.

Migration `0009` **does not remove historical tables or rows**. The retained tables are
`offerings`, `booking_policies`, `resource_requirements`, `resources`, `offering_resources`,
`customers`, `reservations`, `reservation_resources`, `waitlist_entries`, and `outbox_events`.
Their RLS remains enabled, runtime table access is revoked, and dispatcher/inventory function
execution grants are revoked. Old migrations `0001`–`0008` remain unchanged. Historical booking
data may therefore still exist but is inaccessible through current app endpoints. A fresh
installation also replays this history; removing it requires a separately approved database cleanup.

The old worker and scheduler were stopped. Compose no longer defines Beat; the optional
`automation` profile starts an infrastructure-only Celery worker with no registered application
jobs or periodic schedule. Retained worker credentials in an existing local `.env` are unused.
Do not start archived worker commands against this database. No volume reset is needed.

## Known external consistency boundary

Sheets values updates use A1 cell addresses. Stable IDs are resolved freshly for each attempt and
verified after writing, with tenant-scoped idempotency receipts and explicit review on detected
movement. This does not provide a transactional compare-and-swap against simultaneous edits
from Google Sheets or other apps. Protect ID columns, keep IDs immutable, and avoid row/header
reordering during automation. A race can affect an unintended cell before detection; review it
manually rather than blindly retrying. PostgreSQL receipts cannot make Google Sheets atomic.
Do not deploy unattended competing writers until a stronger provider-side coordination scheme
is implemented. Availability reads likewise are observations, not capacity holds or bookings.

## Final validation finding

The deletion-retention regression fails: a later minimal Calendar deletion tombstone overwrites
last-known normalized snapshot metadata with null. HTTP 404/410 also have no recovery response.
Snapshot recovery is not ready for cancellation workflows. See [actual final results](verification.md).
No production fix or Task 05 feature was added during final validation.
