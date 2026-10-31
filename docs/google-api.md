# Google integration API

All routes use the authenticated, selected tenant and require owner/admin membership.
Unsafe methods require the existing trusted Origin and X-CSRF-Token protection. There are no
query/body tenant selectors and no token-read endpoint.

| Method | Path | Result |
| --- | --- | --- |
| GET | `/api/v1/integrations/google` | configured, connection_status, email, updated_at |
| POST | `/api/v1/integrations/google/start` | Authorization URL; JSON body must be `{}` |
| GET | `/api/v1/oauth/google/callback` | Validates state/code or denial and redirects to settings |
| GET | `/api/v1/integrations/google/gmail` | Gmail address and up to five minimal INBOX metadata items |
| GET | `/api/v1/integrations/google/calendars` | Current accessible calendar metadata and local UUIDs |
| POST | `/api/v1/integrations/google/calendars/select` | Verified selection; body `{ "calendar_id": "<local UUID>" }` |
| GET | `/api/v1/integrations/google/events` | Up to five upcoming events for the selected writable calendar |
| POST | `/api/v1/integrations/google/disconnect` | Confirmed disconnected status, or safe pending-revocation error |

Status and data responses never include refresh/access/ID tokens, client secrets, ciphertext,
state records, or PKCE verifiers. The authorization URL necessarily contains Google's public
client ID, scopes, random state/nonce, and PKCE challenge. It contains no session credentials.

Provider errors return `{"detail": "<safe message>", "code": "<stable code>"}`. Examples:
- 400 / OAuth outcome: invalid_state, expired_state, invalid_google_identity, missing_scopes,
  missing_refresh_token, access_denied, code_exchange_failed.
- 403: google_permission_denied, calendar_not_writable, or unauthorized manager membership.
- 404: calendar_not_found for an unknown or foreign local calendar UUID.
- 409: google_reconnect_required, calendar_not_selected, disconnect_pending, account_mismatch.
- 503: google_not_configured, google_unavailable, encryption_key_error, revocation_pending.
- 502: invalid_google_response or google_api_error.

The browser callback uses a 303 redirect with a safe `oauth` outcome instead of returning raw
provider errors. Authentication or membership failures before callback processing use the
normal 401/403/404 responses. Callback query parameters are single-use and must not be logged.
The UI removes the safe outcome query marker from history after displaying its message.
