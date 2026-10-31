# Google integration security and behavior

One Google connection is supported per tenant. A tenant can have multiple cached calendars,
with a partial unique index ensuring exactly zero or one selected calendar. Connections have
explicit disconnected, connected, reconnect_required, and disconnect_pending states.

## Authorization and callback integrity

Only authorized tenant owners/admins can access Google settings, previews, or credential
management endpoints. The application derives tenant identity from the authenticated server
session and revalidates membership. No request can supply an arbitrary tenant context. Staff
may use their regular business dashboard but cannot access Google data or credentials.

OAuth starts with a CSRF-protected POST. The backend requests the six configured scopes,
offline access, explicit consent, state, nonce, and an S256 PKCE challenge. The database stores
only state/nonce digests and an encrypted code verifier. Attempts expire after ten minutes,
are bound to the exact login session, user, and selected tenant, and are single-use. Starting
a fresh attempt supersedes pending attempts for that business. Logout removes attempts via
the session foreign key. The callback rechecks membership and selected tenant, rejects duplicate
or contradictory callback parameters, consumes valid state before contacting Google, and uses
the same server-configured redirect URI for the backend code exchange.

The Google ID token is cryptographically verified against Google's HTTPS JWKS endpoint using
PyJWT with RS256 fixed. Issuer, audience, expiry, issued-at, subject, email verification, nonce,
and authorized party are checked. The Google identity may differ from the ReSlot login email;
it represents the business's connected account. An existing connection must be disconnected
before connecting a different Google subject. Partial grants or missing offline refresh tokens
are rejected without replacing an existing connection.

OAuth callback outcomes redirect only to the first configured trusted frontend origin and a
fixed settings path. The redirect contains a safe outcome code, never a Google error body,
access/refresh/ID token, or authorization code. Callback access-log queries are redacted in
Uvicorn; Nginx suppresses callback access/error logging. Referrer policy is no-referrer for the
callback. Configure any additional production ingress/proxy logging to redact callback queries
as well. Backend responses are no-store. Never enable HTTP body/header debug logging in production.

## Tokens and errors

Refresh tokens are randomized authenticated Fernet ciphertext using GOOGLE_TOKEN_ENCRYPTION_KEY.
The key is separate from the database and stored only in backend configuration. Code verifiers
are encrypted with the same key. Access and ID tokens are never persisted or returned to React.
Access tokens are refreshed on demand for every preview request and kept only in request-local
memory. An API 401 triggers one fresh-token retry; a second 401 or invalid_grant marks the
connection reconnect_required, removes the unusable refresh token, and clears cached calendars.
A rotated refresh token is encrypted before replacing its predecessor.

Google timeouts, permission failures, invalid JSON, and API errors map to safe public codes
without leaking provider payloads. Routes catch provider errors inside the database transaction
so consumed OAuth attempts, revoked-access status, and cleared permissions are committed rather
than silently rolled back. Normal HTTP authorization errors still roll back transactions.

Disconnect immediately invalidates pending attempts and calendar selection. It posts the
refresh token in the body to Google's revoke endpoint (never a token-bearing URL). Confirmed
revocation or Google's invalid_token response clears the stored ciphertext and scopes. A
transient/unconfirmed failure retains the encrypted token only for retry, marks
disconnect_pending, and blocks preview/reconnect use. Retry Disconnect finishes revocation.
Revocation at Google can affect the same account's grants in other tenants using the same OAuth
client; those connections will require reauthorization when Google rejects their refresh.

## Tenant isolation

Migration 0004 enables and forces RLS on google_connections, business_calendars, and
google_oauth_states. Policies require the current transaction's tenant plus an owner/admin
membership for the current authenticated user. Missing or foreign context cannot select or
mutate credentials. Runtime roles cannot bypass RLS or own these tables, verified at startup.
BusinessCalendar's composite (tenant_id, google_connection_id) foreign key prevents attaching
another tenant's connection even through an administrator data path. Responses use explicit
Pydantic schemas that omit credentials, encrypted values, subject identifiers, and attempt data.
An advisory transaction lock serializes Google operations within a tenant; request context
expires on commit/rollback. The application's database credentials remain a trusted boundary,
as documented for Task 02.

## Preview boundaries

Gmail reads account profile plus up to five INBOX messages. Only From, Subject, and Date headers
are requested using metadata format; bodies/snippets/attachments are not returned or stored.
Calendar listing follows page tokens, caches accessible calendars with role/timezone, and removes
stale entries. Selection verifies current calendarList permissions using a Google API read;
only owner/writer roles are accepted. Events preview rechecks access, clears stale selections,
expands recurring events, follows short pages, and returns up to five upcoming events ordered
by start time. All-day dates are preserved. Google calendar timezone is used where supplied;
the business timezone is the fallback if list metadata omits it.

The preview/OAuth integration introduced in Task 03 contains no Gmail watch/push/polling job,
cancellation analysis, mail send, webhook, or AI agent. All previews are explicitly requested by the signed-in user.

## Task 04 managed reservation writes

Reservation synchronization is now implemented separately from the preview routes above.
It creates/patches/deletes only privately marked ReSlot events, with pinned calendar/account
targets, deterministic event IDs, ETags, and a durable outbox. Preview routes still never modify
events. See [reservation design](reservations.md) and [manual write tests](reservation-manual-test.md).
