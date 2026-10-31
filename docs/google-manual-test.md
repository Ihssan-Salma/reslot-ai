# Manual integration test with a real Google account

For Task 04 Sheets and reservation metadata checks, run the
[real integration smoke test](integration-smoke-test.md) after completing this existing Gmail/Calendar path.

Status in this implementation session: **not executed against a real Google account**.
The local environment did not contain GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET, and no
interactive Google consent was supplied. Automated Google HTTP tests are separate and use
mocks, real signed test JWTs, encryption, and actual PostgreSQL isolation.

## Prepare

- Complete [Google Cloud setup](google-setup.md), add your real account as a test user, and
  rebuild/start Compose. Do not send client secrets or tokens in chat or screenshots.
- Use a test account with Gmail enabled. Put at least five harmless messages in its INBOX.
- Have an owned/writable calendar and, if possible, a calendar shared read-only. Create up
  to five harmless upcoming test events using Google's Calendar UI before this test; this
  app will not create any events. An all-day event is useful for checking date formatting.
- Use two browser profiles/private contexts for separate ReSlot accounts (Salon A and Salon B).

## Exercise the acceptance path

1. Register/log into ReSlot AI as Salon A's owner, create/select Salon A, then open
   **Settings** (`/settings/google`). Expect Disconnected and an enabled **Connect Google**.
2. Click **Connect Google**, choose the configured real test account, and approve all requested
   permissions. Check that Google's page displays your app name and expected scopes. After
   returning, expect Connected and the Google email address. No token should appear in page
   content, localStorage, sessionStorage, or frontend JSON. Session/CSRF cookies are HttpOnly;
   OAuth state appears only transiently in Google's authorization URL and callback.
3. Click **Load recent emails**. Compare sender, subject, and date with the latest five INBOX
   messages in Gmail. No body, snippet, attachment, thread analysis, or cancellation label is
   displayed. With an empty test inbox, expect the explicit empty-inbox message.
4. Click **Load calendars**. Compare calendar names and timezones with Google Calendar. Reader
   and free/busy-only calendars must be unselectable. Select an owned/writer calendar, reload
   calendars, and verify exactly one is Selected.
5. Click **Preview upcoming events**. Compare up to five events with that Google calendar in
   start-time order, including all-day dates. With no future events, expect an empty preview.
   The app must not alter or create any event.
6. Reload the page, load calendars again, and verify the selection is persisted. Repeat a
   preview after a restart to verify that the encrypted refresh token still works.
7. In the other browser, log in as Salon B's distinct owner and select Salon B. Settings must
   be Disconnected and must not reveal Salon A's Google email, calendars, or mail. Selecting
   Salon A's local calendar UUID via the API must return 404. Do not share credentials between
   browsers. Automated tests also exercise direct RLS queries and the tenant-aware foreign key.
8. Use Google Calendar's UI to downgrade/remove access to the selected shared test calendar.
   Previewing events must reject it and clear its selection; selecting a different writable
   calendar should restore previews. This check needs a shared calendar rather than a primary
   owned calendar.
9. Open your real Google account's
   [third-party connections](https://myaccount.google.com/connections), remove ReSlot AI access,
   then load email metadata in the app. Expect Reconnect required and no data preview. Click
   **Reconnect Google**, grant consent again, and verify recovery. Reconnection requires
   selecting the calendar again; this avoids reusing stale account/calendar choices.
10. Click **Disconnect**. Expect Disconnected, no local calendar selection/previews, and the
    stored refresh token removed. Check Google Account connections for revoked app access.
    If Google cannot confirm revocation, the app must show Disconnect pending and allow
    **Retry Disconnect**; it must not report success or allow token use in that state.
11. Try loading previews after disconnect; expect a reconnect requirement. Attempting a
    previously used callback URL must fail as invalid state. Do not copy callback codes into
    reports. Declining a new consent request should show a safe declined-access message.

Google revocation can affect all grants/tokens for the same app and Google account. If that
account was used by more than one ReSlot business, the other businesses may also need to
reconnect. Google Workspace policy and consent-screen testing restrictions can affect results.

## Record results

Record date, app version, browser, test steps passed/failed, and sanitized error codes only.
Do not record client secrets, refresh/access/ID tokens, authorization codes, cookies, raw email
content, or customer identifiers. This checklist is intended to be performed by the operator
who owns the real Google test account.
