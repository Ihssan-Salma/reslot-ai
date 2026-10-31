# Task 04 verification

Executed on October 31, 2026, using Python 3.12.7, Node.js 24.14.1, PostgreSQL 17.11,
and Docker Desktop 29.8.2. These are actual local results, not a claim that remote CI ran.

## Automated results

```text
104 passed, 1 warning in 245.29s (0:04:05)
```

No tests were skipped in the final full run. The original 79 authentication/Google tests remain
passing. New tests use real PostgreSQL transactions, restricted database roles, actual SQL
constraints/triggers, and mocked Google HTTP through the real Calendar REST adapter.

The one non-fatal warning was Pydantic's UnsupportedFieldAttributeWarning about FastAPI's
`identifier` alias metadata during the simultaneous capacity-request test. The test still
passed; the warning was not suppressed. Lint/type checks also passed.

Covered scenarios:

- Restaurant tables with capacities 2/4/6, adequate-party allocation, rejection when no suitable
  asset is available, unrelated table bookings, and half-open adjacent intervals.
- Hotel multi-night stays, UTC conversion with retained local dates, room/guest capacity,
  overlapping-stay rejection, and checkout-day arrival according to policy.
- Multiple required types (professional + room), incomplete allocation rejection, and overlap
  rejection across offerings that share a resource.
- Shared capacity uses peak simultaneous quantity, not the sum of disjoint periods. Simultaneous
  HTTP requests (separate login sessions) cannot oversell exclusive or shared offerings.
- Concurrent raw SQL capacity writes are guarded independently of application locks. Direct SQL
  cannot turn an exclusive allocation into shared/released occupancy or understate shared units.
- Cancellation releases capacity and creates its outbox record atomically; failed bookings do
  not leave outbox records. Rescheduling, optimistic versions, staff booking/catalog restriction,
  customer consent edits, date filtering, and timed/nightly waitlist validation pass.
- Independent businesses cannot read or link foreign customers/resources/reservations. Direct
  RLS and tenant-aware foreign-key paths are exercised. API credentials cannot execute global
  outbox claims or become worker/definer roles.
- Google create/update/cancel uses correct private ownership markers, deterministic IDs, ETags,
  and pinned calendar/account targets. A lost accepted response, multiple later edits, retries,
  and lease recovery do not duplicate events. Unrelated/external changes and conditional-write
  races are held as conflicts. Provider failures retain committed reservations and retry safely.
- Europe/Paris and America/New_York conversion, nightly spring DST spans, nonexistent/ambiguous
  local wall times, explicit-offset disambiguation, and all-day Calendar dates pass.

## Other checks

| Check | Actual result |
| --- | --- |
| Fresh migrations and Alembic schema drift | Passed through revision 0008 in disposable test DBs |
| Existing development database migration | Passed additively through 0008; no reset |
| Ruff lint | Passed |
| Ruff formatting | 79 files passed |
| mypy app | Passed; 60 source files |
| pip check | No broken requirements |
| Frontend ESLint / TypeScript | Passed |
| Vite production build | Passed; 37 modules transformed |
| Docker API/worker image | Built |
| Docker frontend image | Built |
| Compose configuration/startup | Passed; API/PostgreSQL/Redis healthy, frontend/worker/Beat running |
| Migration service | Exited 0 |
| Runtime source consistency | Python sources/migrations/config hashes matched workspace |
| Restricted runtime identities | API reslot_app; worker reslot_worker |
| API readiness | HTTP 200, status ready |

The migration revision is queried with the migration administrator, not the API role: the API
intentionally has no SELECT privilege on alembic_version. Development worker concurrency is
bounded to two processes, configurable through CELERY_WORKER_CONCURRENCY; prefetch is one.

## Live HTTP and asynchronous deployment smoke test

A temporary account and restaurant business were created through the actual frontend API proxy,
with an offering, table, compatibility link, customer, and reservation. Actual Celery Beat and
worker processes consumed its PostgreSQL outbox. The result was:

```text
Live HTTP booking saved; Beat/worker processed its outbox; missing Google configuration is visible as NOT_CONFIGURED
Cancellation released capacity immediately; worker completed its no-target cancellation idempotently
All eight reservation-management routes returned HTTP 200
Only the temporary smoke-test account, business, and booking records were removed
```

The reservation remained CONFIRMED despite unavailable Google configuration. Cancellation made
its resource available immediately and the worker completed cancellation as SYNCED without
creating an external event. Cleanup removed only the known temporary business records. Existing
business data and volumes were preserved.

## Manual limits

No real Google account write test was performed: the local environment lacked configured
credentials/consent. Automated Calendar tests mock external HTTP. Follow
[Google setup](google-setup.md) and the [real Calendar/multi-industry checklist](reservation-manual-test.md)
to verify a dedicated real test calendar. Interactive browser rendering was not automated; routes,
compiled assets, API behavior, type checks, and builds were verified.

The frontend dependency inputs did not change. The previous audit checkpoint still records 11
moderate development-tool findings from minimatch GHSA-g3hv-75wg-m249, without high/critical
findings at that checkpoint. No forced dependency downgrade was applied.

Full limits (tenant write serialization, bounded searches/UI catalog sizes, no force-overwrite
conflict resolver, and out-of-scope optimization/business hours) are in reservations.md. No AI,
MCP, Gmail monitoring, or Task 05 work was added.

Reproduce from backend after provisioning roles:

```powershell
..\.venv\Scripts\python scripts/test_backend.py --database
```
