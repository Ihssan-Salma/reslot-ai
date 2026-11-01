# Task 05 verification

Validated November 1, 2026. Official MCP SDK `2.2.0`, Python 3.12, real PostgreSQL
authorization/RLS and mocked Google HTTP. **No real Google integration tests were executed.**

## Final automated results

From the repository root:

```powershell
Push-Location backend
..\.venv\Scripts\python scripts/test_backend.py --database
Pop-Location
```

Actual final output:

```text
164 passed in 177.42s (0:02:57)
```

**164 passed, 0 failed, 0 skipped, 0 errors.** This includes all 126 existing tests and
38 new MCP tests. The focused MCP + automation integration run passed **69 tests in 117.27s**.
The original Task 04 snapshot deletion regression now passes. An intermediate complete run
found a changed generic Google 404 response; the new not-found behavior was limited to event
and message reads, preserving the existing Sheets API contract before this final passing run.

Additional actual checks:

- Ruff check: passed. Ruff format check: **82 files already formatted**.
- Mypy: **no issues in 59 source files**.
- `python -m pip check`: no broken requirements.
- Offline MCP demo: official client connected, discovered all six tools and received validated
  event, candidate and profile responses. Demo `--help` also passed.
- Local updated FastAPI startup completed. `/health` returned `ok`, `/health/ready` returned
  `ready`, and OpenAPI returned HTTP 200. The temporary verification process was stopped.
- Updated Docker backend image built successfully. Compose migration exited 0; backend,
  PostgreSQL and Redis are healthy and the frontend is running. Live container-backed health,
  readiness and frontend root checks returned HTTP 200. This is not a browser E2E test.
- The synthetic MCP demo also passed inside the Linux Docker image; container `pip check`
  found no broken requirements. The application was left running at http://localhost:5173.
- Migration inspection through the dedicated `migrate` service returned `0009 (head)` and
  `No new upgrade operations detected`. An initial attempt under the runtime role was denied
  access to `alembic_version`, as required by the separation of database privileges.
- Test databases replayed migrations `0001`–`0009` and passed Alembic drift checks. Task 05 adds
  no migration and does not delete or truncate application data.

Additional container verification commands executed from the repository root:

```powershell
docker compose build backend
docker compose up -d
docker compose run --rm --no-deps -v "${PWD}/backend/scripts:/app/scripts:ro" backend python scripts/mcp_demo.py
docker compose exec backend python -m pip check
docker compose run --rm --no-deps migrate alembic current
docker compose run --rm --no-deps migrate alembic check
```

## Architecture and coverage

See [MCP architecture, contracts and exact startup commands](mcp.md) and
[created/modified files](files.md). Active application modules retain authentication, tenant
membership, OAuth, Sheets, Calendar normalization, Gmail and minimal integration persistence.
New `app/mcp` modules implement the server, trusted session context, six tools, schemas and
reusable official SDK client. No frontend changes, agent workflows, outbound communication or
booking management features were added. Previously archived booking code remains archived;
Celery/Redis infrastructure and all historical migrations remain retained.

Protocol tests cover discovery, typed responses, availability, deterministic eligibility,
customer/entry lookup, Gmail HTTP and bounded MIME parsing, invalid arguments, missing resources,
provider failures, sanitized errors, session logout/expiry/switching, membership removal/role
downgrade, Google revocation persistence, concurrent tenants with distinct configured sheets,
foreign resource arguments, and snapshot subject/event/payload/permission checks.
Existing OAuth consent, RLS, schema, generic industry and CSRF tests continue to pass.

Snapshot recovery preserves last valid metadata and requires review; it does not prove current
availability. Resource-specific capacity is deliberately uncertain. Free-text preferences need
review. Only a trusted local in-process/stdio MCP transport is provided; there is no HTTP MCP
endpoint. Existing Sheets external-row-reordering limitations remain; MCP exposes no write tools.

## Real-account smoke test (manual, not executed)

Configure a dedicated Google test account using [Google setup](google-setup.md), authorize Sheets,
configure the spreadsheet and selected calendar, and add synthetic records plus a marked event.
From repository root, replace placeholders and use your source record's actual dates:

```powershell
docker compose up --build -d
Push-Location backend
..\.venv\Scripts\python scripts/mcp_demo.py --real --email owner@example.com --tenant-id YOUR_BUSINESS_UUID --event-id YOUR_EVENT_ID --customer-id YOUR_CUSTOMER_ID --offering "Dinner" --start-at "2027-01-02T17:00:00Z" --end-at "2027-01-02T18:00:00Z" --party-size 2 --quantity 1
Pop-Location
```

Add `--message-id YOUR_MESSAGE_ID` for Gmail. Passwords are prompted securely; output omits real
contact data and bodies. Repeat with two separate businesses/private resources to check real
provider isolation. The complete [manual guide](mcp.md) includes local server startup and client
connection. Google consent, provider ACLs and real responses still need this manual validation.

---

# Historical Task 04 final validation (superseded by Task 05 above)

Validated on October 31, 2026. Status: **not fully signed off; snapshot deletion retention fails**.
This validation added a regression test and strengthened the existing OAuth reauthorization test.
No new production features were implemented during final validation. Task 05 was not started.

## Complete backend suite

Command, from repository root:

```powershell
Push-Location backend
..\.venv\Scripts\python scripts/test_backend.py --database
Pop-Location
```

Actual final result:

```text
1 failed, 125 passed in 107.63s (0:01:47)
```

126 collected: **125 passed, 1 failed, 0 skipped, 0 errors**. Exit code 1.
An earlier complete run before adding the deletion regression passed all 125 tests in 64.20s.
That earlier result does not supersede the failing final regression run.

Failing test:
`tests/test_automation_integrations.py::test_recorded_snapshot_preserves_metadata_after_deletion_tombstone`.
It records a fully normalized event, then mocks Google's minimal deletion tombstone
`{"id":"event-1","status":"cancelled"}`. Reading the tombstone replaces the previously stored
normalized payload with null. The preservation assertion fails. The expected and actual values
were read from real PostgreSQL; Google HTTP alone was mocked.

Initial validation attempts were interrupted by host memory pressure and stopped Docker
services. PostgreSQL/Redis were restarted without volume deletion, and the full suite was rerun.
The final result above includes no infrastructure errors.

## Database and startup validation

- Fresh disposable test databases replayed migrations `0001` through `0009`, including historical
  migrations, and passed Alembic metadata drift checks. Only disposable databases were dropped.
- The actual project database upgraded from `0008` to `0009` using `docker compose run --rm migrate`.
  Before/after row counts and sorted-content fingerprints matched for **all 17 pre-existing tables**,
  preserving all **4 pre-existing rows**. The version row changed as expected and new tables were
  added. No application table/row was dropped, deleted, or truncated.
- `alembic check` reported `No new upgrade operations detected` on the migrated project database.
- Updated backend/frontend images built successfully. Compose startup completed; API, PostgreSQL
  and Redis were healthy, frontend running, migration service exited 0.
- Live `/health` and `/health/ready`: HTTP 200. Frontend `/` and `/settings/google`: HTTP 200;
  both referenced JS/CSS assets: HTTP 200. These are HTTP/asset checks, not browser E2E tests.
- Live Sheets endpoint through Nginx without authentication: HTTP 401.
- Runtime database-role and forced-RLS assertions passed inside the running backend container.
- Backend Ruff check/format passed; mypy passed for 51 source files. Frontend ESLint and TypeScript
  passed; Vite production build passed. The smoke CLI's `--help` command also ran successfully.
- The backend was rebuilt after detecting a stale Calendar module in the earlier image.
  The running container and workspace now have matching fingerprints across all 51 app files;
  health and runtime security checks passed again after restart.

Startup commands from repository root:

```powershell
docker compose up --build -d
docker compose ps -a
Invoke-RestMethod http://localhost:8000/health/ready
Start-Process http://localhost:5173
```

## Tenant isolation and OAuth

Passing tests verify one spreadsheet configuration per authorized tenant, missing configuration
in another business, invalid tenant switches, cross-tenant Google-connection foreign-key rejection,
CSRF rejection and unauthorized access. RLS tests seed records in every new tenant-owned table,
verify the owner can see them, then verify another user cannot read or insert them even when a
foreign tenant context is supplied directly to the restricted database session. Data operations
resolve the file from authorized tenant configuration; they do not accept a caller-supplied file ID.

OAuth tests request the seven scopes including Sheets, retain working Gmail access for legacy
six-scope connections, return `sheets_reconsent_required` for Sheets, then complete mocked Google
reauthorization and verify Sheets configuration and Gmail work. Existing invalid-state, missing
refresh token, revocation, identity, permission and cross-tenant regression tests pass.

These are mocked Google HTTP tests with real JWT verification, encryption and PostgreSQL.
**No real Google integration test ran.** The local environment lacked a complete configured OAuth
client/encryption setup and no interactive Google consent was available. Google Cloud permissions,
real file sharing and real Calendar behavior remain manual validation requirements.

## Calendar and snapshot finding

Normalization has no business-type input or industry-specific model branch. Passing tests cover
restaurant dining, hotel stays, salon appointments, consultation and desk passes using the same
metadata contract; manual description blocks, all-day DST intervals, ambiguous times, malformed
metadata and unmarked events are covered. Unmarked opaque events block availability, but do not
become reservations just because their title resembles one.

A successful snapshot contains all requested normalized fields and could provide last-known
metadata while retained. However, this implementation overwrites that payload on a later
unrecognized/minimal deletion tombstone. An unmarked tombstone is currently classified ignored,
not a recoverable reservation. HTTP 404/410 do not provide a snapshot-recovery response either;
the current Google request fails before snapshot lookup. Consequently **deletion recovery cannot
be confirmed and is a blocker**. Last-known valid metadata must be preserved separately from
latest source/deletion state before claiming reliable recovery. No fix was introduced in this
validation-only pass.

## Active architecture and retired components

Active modules: auth/tenant routes and services; Google OAuth/Gmail/Calendar service and transport;
`GoogleSheetsService`; `CalendarReservationService`; tenant-filtered repositories; auth/Google
configuration and five minimal integration-state models; minimal React auth/business-selection
and integration settings. PostgreSQL, Redis, Alembic and optional idle Celery remain available.
LangGraph/Groq/MCP modules remain placeholders with no running agent or tool server.

In the running backend container, obsolete booking/catalog/reservation/Calendar writer task
modules were absent, application Celery tasks were empty, Beat schedule was empty, and Redis
broker connectivity passed. The previous worker and Beat containers remain **stopped**. There is
no active scheduler and no application jobs are executed. The optional automation profile retains
reusable background infrastructure. Live OpenAPI has no reservation/customer/catalog/resource CRUD.

Archived under `legacy/reservation_management/`: prior booking CRUD/services, catalog/allocation,
customer/wait-list management, Calendar writer/outbox task, management pages, superseded tests/docs.
Retained: migrations `0001`-`0008`, historical SQLAlchemy metadata for Alembic only, old tables and
all their data. Migration `0009` revokes runtime access to legacy tables and dispatcher functions
without deleting them. See [the complete retained inventory](architecture-pivot.md).

## Remaining issues and real-account commands

1. Snapshot metadata retention/recovery after deletion: failing regression above; do not treat
   Task 04 as fully validated or proceed to Task 05 on an all-green assumption.
2. Real Google consent and live Sheets/Calendar tests: not executed.
3. Sheets cell writes cannot guarantee atomicity against simultaneous external row reordering.
   Stable-ID resolution, idempotency receipts and post-write verification detect common races;
   immutable IDs and avoiding competing structural edits remain necessary. See the
   [documented consistency boundary](architecture-pivot.md#known-external-consistency-boundary).

Complete [Google Cloud setup](google-setup.md), enable Sheets API, add the seventh scope,
reconnect old accounts, configure a private [synthetic Sheet](google-sheets.md), select a writable
calendar and manually create a marked [reservation event](calendar-reservation-format.md).
From repository root, replacing the example arguments:

```powershell
.\.venv\Scripts\python backend/scripts/smoke_integrations.py --base-url http://localhost:5173 --email owner@example.com --tenant-id YOUR_BUSINESS_UUID --event-id YOUR_EVENT_ID
```

For an explicitly opted-in synthetic status update followed by restoration:

```powershell
.\.venv\Scripts\python backend/scripts/smoke_integrations.py --base-url http://localhost:5173 --email owner@example.com --tenant-id YOUR_BUSINESS_UUID --event-id YOUR_EVENT_ID --entry-id wait-001 --write-status OFFERED
```

Passwords are prompted securely. Do not supply Google tokens/secrets on the command line.
The default smoke command is read-only against Google; the opt-in write touches only a synthetic
Sheets status, never Calendar events. If it fails, manually review the Sheet before retrying.
Use two isolated browser profiles/accounts and distinct private Sheets for the live isolation
checks in [the full smoke guide](integration-smoke-test.md). Passing this smoke alone does not
cover the known deletion-recovery failure.
