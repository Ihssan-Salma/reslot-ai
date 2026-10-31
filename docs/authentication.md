# Authentication and tenant isolation

## Request flow

1. GET `/api/v1/auth/csrf` sets a random HttpOnly nonce cookie and returns an HMAC CSRF token
   bound to the current session cookie. Responses are not cacheable.
2. POST registration/login includes JSON, an exact trusted Origin, and X-CSRF-Token.
   Registration normalizes email and hashes a 12?128 character password with Argon2id
   (64 MiB, three iterations, parallelism four). Login verifies a dummy hash for missing users.
3. Login creates a 256-bit random opaque session cookie; only its SHA-256 digest is stored.
   The previous browser session is deleted on successful login. Credentials never appear in
   response bodies. CSRF tokens must be fetched again after login because the binding changed.
4. Every authenticated request verifies session expiry, loads its user, and starts a fresh
   transaction-local user context. A session row lock serializes changes to that session.
5. Tenant routes verify membership before setting transaction-local tenant context. Selection
   is persisted on the server-side session and reauthorized on every dashboard request.
6. Logout deletes the session record and expires both cookies. Stolen old cookies cannot
   be replayed after revocation. Sessions expire absolutely after SESSION_TTL_SECONDS.

CSRF is a separate anti-forgery value, not an authentication credential. The nonce and session
cookies are HttpOnly. Frontend code fetches the token for each mutation and never reads or
stores session cookies. Secure mode uses host-prefixed cookies. SameSite=Lax supplements
Origin and token checks; it is not the sole CSRF protection.

## API

| Method | Path | Access |
| --- | --- | --- |
| GET | `/api/v1/auth/csrf` | Public; returns CSRF token |
| POST | `/api/v1/auth/register` | Public + CSRF; creates user, no implicit login |
| POST | `/api/v1/auth/login` | Public + CSRF; rotates server-side session |
| POST | `/api/v1/auth/logout` | Authenticated + CSRF |
| GET | `/api/v1/auth/me` | Authenticated profile; no password/session digest |
| GET | `/api/v1/tenants` | User-scoped membership discovery |
| POST | `/api/v1/tenants` | Authenticated + CSRF; atomically creates business and owner |
| POST | `/api/v1/tenants/switch` | Authenticated + CSRF; verifies supplied ID before selection |
| GET | `/api/v1/dashboard` | Selected, currently authorized, active business |
| GET | `/api/v1/tenants/{id}/dashboard` | Membership verified before installing tenant context |

Unauthorized requests return 401; failed membership checks return 404 to avoid tenant
existence disclosure. Suspended businesses return 403. No selection returns 409. Schemas
reject extra fields such as client-supplied owner/user IDs and roles. Timezones are validated
against IANA tzdata. Users may own and belong to multiple businesses; owner/admin/staff are
stored and returned. Membership invitation/editing is not part of this task.

## PostgreSQL defense in depth

The API and worker use `reslot_app`, a login granted `reslot_runtime`. Both lack SUPERUSER,
BYPASSRLS, CREATEDB, and CREATEROLE. Startup checks the login and every role it can become,
requires separate tenant-table ownership, and verifies ENABLE + FORCE ROW LEVEL SECURITY.
The runtime has SELECT on tenants/memberships, but no direct INSERT, UPDATE, DELETE, TRUNCATE,
or schema CREATE privileges on tenant tables. Application code cannot grant its own membership.

Tenant and membership SELECT policies require the authenticated user context and membership;
when tenant context is installed they additionally restrict rows to that business. Missing
user context denies all rows. Forging only a tenant setting cannot override membership.
User-scoped business discovery deliberately runs without a selected tenant and lists only
that user's memberships. Context is set with `set_config(..., true)` inside an explicit
transaction, reset at request entry, and cleared automatically at commit/rollback. The
transaction commits before the HTTP response is sent. No context API is exposed to clients.

`create_business(text,text,text)` is a narrow SECURITY DEFINER function with a fixed search
path and no dynamic SQL. It generates the tenant UUID itself and atomically inserts that
tenant and an owner membership for the verified transaction user. Its NOLOGIN owner,
`reslot_creator`, has only INSERT privileges on those two tables and remains subject to RLS
through insert-only policies. It cannot read or change existing tenants. Runtime users can
execute this function but cannot SET ROLE to its owner. PUBLIC cannot execute it. This avoids
using an RLS-bypassing role even for initial ownership creation.

User/session tables are global authentication infrastructure. Runtime access is limited to
server use; they are never exposed as generic SQL or data APIs. Database credentials and
server-established user context are trusted boundaries. RLS protects omitted tenant filters
and incorrect tenant selection, not a fully compromised backend or stolen database credentials.
Future tenant-owned tables must add tenant_id, indexes, tenant-aware relationship constraints,
forced RLS, and isolation tests before receiving runtime grants. No appointment entities exist yet.

## Operations and verification

Migrations use a separate cluster-privileged administrator and provision the runtime login.
Only the migration service receives administrator credentials. Production should run migration
jobs separately and supply secrets through its secret manager. Do not run the API as the
migration owner. Apply every revision through 0008 before startup. Google connection tables additionally use
manager-only tenant RLS; see [Google security](google-security.md).

The PostgreSQL test fixture creates a separate disposable database. Tests cover two independent
accounts and Salon A/B, ownership, authorized staff/admin membership, switching, revoked
membership, suspended businesses, CSRF and Origin failures, expired/revoked sessions, hashed
storage, cookies, direct SQL isolation, context cleanup, forced RLS, forbidden table writes,
and inability to become the business-creator role. See verification.md for actual results.

Sources: [PostgreSQL row security](https://www.postgresql.org/docs/17/ddl-rowsecurity.html),
[OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html),
and [OWASP CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

Task 04 extends forced RLS to every booking table. Catalog/business configuration requires
owner/admin membership; ordinary members can manage customer/reservation/waitlist records.
The worker login is separate and derives context only through restricted outbox claims;
see [reservation security and locking](reservations.md).
