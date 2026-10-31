# Generic reservation architecture

Task 04 adds one reservation system for every industry. `Tenant.business_type` is descriptive;
no validator branches on restaurant, hotel, salon, consulting, or coworking names. Newly created
businesses default to `other`; existing business records and migrations 0001?0004 are preserved.

## Domain model

| Entity | Purpose |
| --- | --- |
| Offering | A reservable product, booking mode, optional duration bounds and concurrent quantity limit |
| BookingPolicy | Typed resource requirement flag, nightly check-in/out times, stay bounds, turnaround minutes |
| ResourceRequirement | Typed per-offering minimum count for arbitrary resource type names |
| Resource | A named human/physical asset, optional capacity, EXCLUSIVE or SHARED allocation |
| OfferingResource | Tenant-aware many-to-many compatibility between offerings and resources |
| Customer | Business-owned contact information and explicit notification consent |
| Reservation | Customer/offering, UTC interval, optional local stay dates, status, quantity, pinned Calendar target |
| ReservationResource | Resource allocations, quantities, active/released state, authoritative occupied interval |
| WaitlistEntry | Customer/offering preferences, date/time window, optional preferred resource, quantity/status |
| OutboxEvent | Immutable versioned Calendar snapshot, idempotency key, attempts, lease, retry time, safe error |

Every entity has tenant_id, UUID identifiers, and forced RLS. Composite foreign keys include
tenant_id, preventing cross-business references even outside the API. ORM relationships expose
catalog links, customers, offerings, allocations, waitlist preferences, and outbox history.
Essential booking rules live in typed columns and validated Pydantic contracts, not opaque JSON.
Outbox JSON is a derived external-event snapshot, never the source of booking policy.

## Policies and allocation extension points

`services/booking_policies.py` dispatches by TIMED, NIGHTLY, or CAPACITY through a policy protocol.
Timed policies accept explicit UTC offsets or unambiguous business-local wall times. End time
may be omitted only when a default duration exists. A default without min/max bounds is fixed;
with bounds it is a suggested duration. Flexible offerings may have no default at all.
Nightly policies require local check-in/check-out dates, validate stay length, and resolve the
configured local check-in/out times through the business IANA timezone into UTC. Stay dates
remain stored separately and drive all-day Google events. DST gaps and ambiguous wall times
are rejected; timed bookings can disambiguate with explicit offset timestamps.

`AvailabilityService` coordinates duration policy, offering capacity, compatible/active resource
inventory, and required resource types. `allocation.py` defines a strategy protocol with explicit
and greedy compatible allocation implementations. The default chooses the smallest sufficient
compatible capacity for each required type. One professional plus one room is configuration,
not a salon-specific rule. Custom strategies can replace automatic selection without duplicating
reservation persistence, authorization, or synchronization.

`quantity` consumes offering/shared-resource capacity units; `party_size` checks an individual
resource's accommodation capacity. Each selected exclusive resource consumes one asset slot.
Quantity does not automatically combine tables, split guests across rooms, or optimize allocation.
Explicit multi-resource selection is supported. If compatible resources exist, at least one must
be assigned; configured type counts must always be satisfied. Resource-free offerings are allowed
when no resources are required; use CAPACITY with a limit for bounded shared inventory.

## Transactions and double-booking prevention

All occupancy uses half-open intervals [start,end). Resource turnaround extends occupied_until;
therefore an adjacent booking is allowed exactly when the previous occupied interval ends.
PENDING and CONFIRMED consume capacity. CANCELLED, COMPLETED, and NO_SHOW release allocations.
Cancellation/release and its outbox write commit atomically.

A tenant-level transaction advisory gate serializes booking/configuration changes. The engine
also locks offering and compatible resource inventory rows through a narrow NOLOGIN definer
function. This function checks tenant membership under RLS; it does not bypass row security.
PostgreSQL exclusion constraints prevent overlapping active exclusive allocations regardless
of application prechecks. Database triggers derive allocation time/status/exclusivity/quantity
from the parent reservation and resource; caller-supplied flags cannot disable exclusion checks.
Shared/offering capacity guards lock inventory and use an endpoint sweep to check peak concurrent
quantity, correctly handling disjoint intervals and simultaneous start/end boundaries. Inventory
version writes force stale concurrent snapshots to fail instead of overselling. Live capacity,
allocation mode, resource type, and offering duration/capacity changes are guarded.

Validated domain conflicts return safe 409 responses; invalid inputs return 422 and foreign IDs
return 404. Transactions commit before HTTP responses. Reservation sync_version provides optional
optimistic concurrency checks for edits and rescheduling. Existing unrelated resources may both
be booked successfully, although the current tenant-wide gate deliberately favors correctness
over parallel write throughput. Finer inventory locks can replace that gate in a later optimization.

Configuration is restricted to owners/admins. All current members can manage customers,
reservations, and waiting lists. Staff inventory checks use the constrained locking helper;
they do not gain catalog-edit or Google-credential access. Business timezone changes are blocked
while active reservations exist to avoid reinterpreting their local dates.

## Reliable Google Calendar outbox

Reservation creation, modification, assignment, status change, and cancellation enqueue a
snapshot keyed by reservation UUID and sync_version in the same PostgreSQL transaction. The API
never calls Google while saving a reservation. Redis availability is not required to commit it.
Celery Beat emits a drain task every ten seconds, and a separate worker claims database records
with SKIP LOCKED, a lease token, and expiry. A crash leaves recoverable leased work. Temporary
provider errors retry with exponential backoff, up to eight attempts, then wait for manual retry.
Older versions are superseded rather than overwriting newer reservation state.

The API uses reslot_app. Worker/Beat use reslot_worker with a separate password. Both are
NOSUPERUSER/NOBYPASSRLS; the worker additionally has execute-only dispatch privileges. A narrow
NOLOGIN function can discover due outbox rows and derive an active tenant's current owner/admin.
The worker installs that trusted context, verifies membership again, and then reuses GoogleService.
External task payloads never choose a tenant/user context. API credentials cannot claim the global
queue, become the worker role, or read another tenant's records.

Before any external write, the worker persists the selected Google calendar, connected Google
subject, and deterministic event ID. A retry cannot silently move the reservation if the business
later selects another calendar. Events use reservation UUID hex IDs plus private tenant/reservation/
version markers. Insert retries recover the same event after a lost response. Updates/deletes
verify ownership markers and use ETag If-Match conditions; unrelated, externally removed, or
externally modified events are held as CONFLICT rather than overwritten. Previous immutable
snapshots permit recovery when Google accepted a write but the worker did not record success.
Only managed summary, description, timing, and private ownership markers are patched. No attendees
are added, and sendUpdates=none avoids invitation emails. Customer email/name is not copied into
calendar event payloads.

Cancellation removes only the matched managed event. Cancelling a booking that never had a
Calendar target requires no Google write. The committed reservation remains valid when OAuth,
calendar permissions, or Google availability fail. NOT_CONFIGURED/FAILED/CONFLICT are visible
alongside safe errors, attempt counts, and Retry actions. Restore access to the original pinned
calendar/account before retrying; switching settings affects future targets, not existing ones.

## CRUD and retention

Offerings/resources deactivate instead of deleting referenced history. Customer deletion is
rejected when reservation/waitlist history exists. Deleting an active reservation cancels and
soft-archives it, retaining its outbox until Calendar cancellation can finish; terminal reservations
can be archived without reopening them. Waitlist entries support ACTIVE/INACTIVE/FULFILLED and
removal; they never allocate resources, send messages, or perform candidate matching.

List APIs support pagination. Reservation filtering accepts UTC-offset ranges, business-local
exclusive date ranges, status, offering, or resource. Available-period search evaluates at most
200 candidates within a 31-day horizon and returns a continuation cursor. Calendar UI displays
up to 500 overlapping reservations per month. Catalog UI loads up to 500 records per category;
large deployments should add catalog search/pagination before using those screens at scale.

No business-hour/holiday engine, billing, table combining, room-assignment optimization, arbitrary
Google event import, monitoring, AI agent, or MCP server is implemented. External conflicts are
review-only; there is no force-overwrite action. Real Google write verification still requires an
operator's configured account and test calendar; see reservation-manual-test.md.

References: [PostgreSQL range exclusions](https://www.postgresql.org/docs/17/rangetypes.html),
[Calendar event IDs](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert),
[Calendar conditional modifications](https://developers.google.com/workspace/calendar/api/guides/version-resources),
and [Celery periodic tasks](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html).
