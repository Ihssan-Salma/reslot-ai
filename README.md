# ReSlot AI

Multi-tenant AI automation foundation for recovering last-minute cancellations across restaurants,
hotels, salons and other businesses. Calendar owns reservations, Gmail owns messages, and a
business's authorized Google Sheet holds customers and waiting lists. This is not a booking
management system. LangGraph/Groq/MCP orchestration and agents are future work.

Task 04 preserves authentication, tenant isolation and existing Google integrations, and adds
Sheets read/lookup/status operations plus read-only Calendar normalization and availability.
No reservation CRUD, customer dashboard, monitoring, automated offers or Calendar writes run.

## Start with Docker

Requirements: Docker Desktop Linux engine and Compose v2. PowerShell, repository root:

```powershell
# First installation only; preserve an existing .env.
Copy-Item .env.example .env
```

Edit `.env`: choose separate URL-safe POSTGRES_PASSWORD and APP_DATABASE_PASSWORD and match
MIGRATION_DATABASE_URL/DATABASE_URL for local development/tests. Keep existing passwords and
Fernet keys when upgrading. Never commit `.env`, OAuth secrets or downloaded credentials.

```powershell
docker compose up --build -d
docker compose ps -a
Invoke-RestMethod http://localhost:8000/health/ready
Start-Process http://localhost:5173
```

Compose starts PostgreSQL, Redis, one-shot migrations through `0009`, FastAPI and the frontend.
Register/log in, create/select a business, open Settings at http://localhost:5173/settings/google.
API docs: http://localhost:8000/docs. Runtime uses a restricted non-owner database role.

```powershell
docker compose logs backend migrate
docker compose run --rm migrate alembic current
docker compose down
```

The named PostgreSQL volume persists. Do not reset it to upgrade. Editing POSTGRES_PASSWORD
cannot change an initialized admin password; migrations provision the runtime password.
The previous booking worker/Beat are retired. On upgrades stop the exact old containers
`reslot-ai-worker-1` and `reslot-ai-beat-1` if present; do not start archived commands.
An optional `docker compose --profile automation up -d worker` runs idle Celery infrastructure
with no application tasks or periodic schedule. Historical booking tables/data remain intact,
with runtime access revoked. See [the pivot inventory](docs/architecture-pivot.md).

## Local development

Python 3.12+ and Node 24 recommended, plus PostgreSQL/Redis:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e "./backend[dev]"
docker compose up -d postgres redis
Set-Location backend
..\.venv\Scripts\python -m app.db.migrate
..\.venv\Scripts\uvicorn app.main:app --reload
```

Stop the Compose backend first if it occupies port 8000. In a second root terminal, stop the
Compose frontend if it occupies 5173, then:

```powershell
Set-Location frontend
npm ci
npm run dev
```

Vite/Nginx proxy `/api` to FastAPI. Use localhost:5173 consistently for the trusted Origin.
Use 127.0.0.1 in database URLs if the localhost IPv6 endpoint is unavailable.

## Verification

After provisioning roles/migrations, from repository root:

```powershell
Set-Location backend
..\.venv\Scripts\ruff check .
..\.venv\Scripts\ruff format --check .
..\.venv\Scripts\mypy app
..\.venv\Scripts\python scripts/test_backend.py --database
Set-Location ../frontend
npm run lint
npm run typecheck
npm run build
```

The helper reads root `.env` runtime/admin URLs, creates a uniquely named disposable database,
replays migrations, checks schema drift, and removes only that test database. Application data
is never truncated. Google HTTP is mocked; actual PostgreSQL RLS/constraints, signed JWT
verification, encryption and services run. Plain pytest without test URLs skips database tests.

Complete [Google setup](docs/google-setup.md), reconnect for Sheets consent, import the
[synthetic template](docs/google-sheets.md), configure its ID and select a calendar. Create a
marked event in Google Calendar using [this format](docs/calendar-reservation-format.md).
From the root, run (replace example arguments):

```powershell
.\.venv\Scripts\python backend/scripts/smoke_integrations.py --base-url http://localhost:5173 --email owner@example.com --tenant-id YOUR_BUSINESS_UUID --event-id YOUR_EVENT_ID
```

The password is prompted securely. See [the real integration smoke guide](docs/integration-smoke-test.md)
for explicit synthetic writes and isolation checks. Real Google access requires your own OAuth
client, encryption key, enabled APIs, test user and interactive consent. No secrets belong in
VITE_* variables. Production requires HTTPS, Secure cookies and exact trusted origins.

## Validation status

Final Task 04 validation: **125 passed, 1 failed, 0 skipped**. The failing regression shows
that a deletion tombstone can erase previously saved Calendar snapshot metadata. Task 04 is
not fully signed off; no Task 05 features have been started. Real Google smoke tests have not
been executed. See [the full results and remaining issues](docs/verification.md).

## Documentation

- [Architecture and retained history](docs/architecture-pivot.md)
- [Sheets schema, consent, statuses and concurrency limits](docs/google-sheets.md)
- [Calendar reservation convention](docs/calendar-reservation-format.md)
- [Actual verification results](docs/verification.md)
- [Source file inventory](docs/files.md)

Sheets writes use stable IDs and verification, but cannot be atomic against simultaneous external
row reordering. Keep IDs immutable and avoid competing structural edits; detected races require
review. Availability is an observation, not a booking hold. No destructive database cleanup ran.
