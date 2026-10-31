# Real Google integration smoke test

Automated tests mock Google HTTP. This smoke test requires a real test account and interactive
consent; mock results do not verify Google Cloud configuration.

## Preparation

1. Complete [Google setup](google-setup.md), enable Sheets API, add all seven scopes and your test
   user. Keep real secrets in ignored `.env`, preserve the encryption key, rebuild/restart Compose.
2. Log in, select a business, open Settings and connect/reconnect Google with consent for Sheets.
3. Import the [synthetic template](google-sheets.md) into a private spreadsheet shared as Editor
   with that Google account. Configure its ID; health should show 3 customers and 3 entries.
4. Load calendars and select a writable dedicated test calendar. Use Google's UI to create a
   harmless event with the [documented reservation description](calendar-reservation-format.md).
   Keep its interval below 31 days. The app itself must not modify/create events.
5. Obtain its Calendar API event ID from the authenticated preview response
   `/api/v1/integrations/google/events` (five upcoming events) or your authorized Google tooling.
   An event ID is not the encoded `eid` browser URL or calendar ID. Obtain the business UUID
   from `/api/v1/dashboard` in browser developer tools. Never copy cookies/tokens into docs.

## Read-only command

From repository root with backend dependencies installed, replace example arguments:

```powershell
.\.venv\Scripts\python backend/scripts/smoke_integrations.py --base-url http://localhost:5173 --email owner@example.com --tenant-id YOUR_BUSINESS_UUID --event-id YOUR_EVENT_ID
```

The script prompts for a hidden password, authenticates with cookie/CSRF auth, switches tenant
with membership validation, checks Gmail, Sheets schemas/read/lookups, Calendar normalization
and availability, and logs out. Only PASS summaries are printed. A busy availability result is
valid because the test event occupies its interval. Unmarked/review events fail the reservation
assertion instead of being silently treated as reservations.

## Explicit synthetic write

Use a synthetic row whose initial status differs from OFFERED. Do not reorder/edit during this test:

```powershell
.\.venv\Scripts\python backend/scripts/smoke_integrations.py --base-url http://localhost:5173 --email owner@example.com --tenant-id YOUR_BUSINESS_UUID --event-id YOUR_EVENT_ID --entry-id wait-001 --write-status OFFERED
```

This changes the synthetic status, verifies success, then restores its original status. No
Calendar writes occur. If any write fails or an external row race is detected, stop and manually
review the test sheet; restoration is not guaranteed during an outage. Inspect the final cell.

## Manual isolation and error checks

- Use another private browser profile, different ReSlot user/business, distinct private Sheet and
  Google account. Each must see its own configuration only. Switching to the other's UUID is 404;
  unknown record IDs are 404. Unshared files must fail without borrowing another tenant's grant.
- Rename a required header, enter consent `yes`, or use a native date serial: health must reject
  with 422 without disclosing source cells. Restore valid data afterwards.
- A read-only calendar is unselectable. Revoking write access to the selected calendar must be
  detected on the next permission check. Unmarked events are ignored for normalization but
  still block availability when opaque. Invalid marked events return review.
- Revoke Google access through account permissions: refresh must require reconnecting. Disconnect
  in settings, verify subsequent data access fails, then reconnect/reselect if continuing.
- Old grants without Sheets consent must request reconnection for Sheets while preserving old
  Gmail/Calendar access. Confirm no OAuth secrets/tokens enter browser storage or JSON responses.

## Automated integration commands

From the repository root after migration/role provisioning:

```powershell
Set-Location backend
..\.venv\Scripts\python scripts/test_backend.py --database
..\.venv\Scripts\python scripts/test_backend.py --database tests/test_automation_integrations.py tests/test_calendar_normalization.py
```

These use mocked Google HTTP and a disposable PostgreSQL database, never application truncation.
The helper reads DATABASE_URL/MIGRATION_DATABASE_URL from root `.env` unless TEST_DATABASE_URL
and TEST_ADMIN_DATABASE_URL override them. The admin must be able to create disposable databases
and replay historical migrations. Actual results and real-consent availability are recorded in
[verification.md](verification.md).
