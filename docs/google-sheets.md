# Google Sheets integration

Enable **Google Sheets API** in the existing OAuth project, add
`https://www.googleapis.com/auth/spreadsheets` to the consent screen, restart the app, and use
**Reconnect Google** to grant it. Existing Gmail/Calendar grants keep working without this scope;
Sheets responds `sheets_reconsent_required` until consent is granted.

Google recommends `drive.file` for selected files. That scope only authorizes files created or
opened through the application, normally through Google Picker. This backend authorization-code
flow accepts an existing file ID and has no Picker/file-open flow, so it requests the Sheets
read/write scope instead. The consent screen must disclose that this scope can access all
spreadsheets the account can access. ReSlot restricts operations to the one file configured for
the authenticated business; this application restriction does not narrow Google's token grant.
Never make a spreadsheet public. Share it with the connected Google account as Editor for writes.
See Google's [Sheets scope guidance](https://developers.google.com/workspace/sheets/api/scopes).

## Create the template

1. Create a private Google spreadsheet. Create tabs named exactly `Customers` and `WaitingList`.
2. Import [Customers.csv](templates/Customers.csv) and [WaitingList.csv](templates/WaitingList.csv)
   into their respective tabs. These records are synthetic, with example.com email addresses.
3. Disable **Convert text to numbers, dates, and formulas** during CSV import. Format IDs, phone,
   and date/time columns as **Plain text**. Consent can be checkbox booleans or literal TRUE/FALSE.
4. Keep row 1 as the exact headers below. Reordered columns work; duplicates, missing, or extra
   populated columns are rejected. Blank data rows are ignored. Maximum 10,000 data rows per tab.
5. Copy the ID from `https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit`. In business
   settings, paste only the ID and select **Save and validate**. Both schemas and all records
   must validate before configuration is saved. **Check spreadsheet health** reports counts.

```text
Customers:
customer_id, full_name, email, phone, notification_consent

WaitingList:
entry_id, customer_id, requested_offering, preferred_start, preferred_end,
party_size, quantity, additional_preferences, status
```

IDs are immutable unique text identifiers of 1–100 letters, digits, `_` or `-`, starting with
a letter or digit. Customer and entry IDs have separate namespaces. Every waiting-list customer
must exist in Customers. Names/offerings are nonempty (maximum 200 characters); emails must
validate. Phone is optional text, never a numeric cell. Consent accepts actual booleans and
TRUE/FALSE text only; a false value is preserved. No offers are sent by this task, and future
agents must filter for affirmative consent rather than assuming every customer is eligible.

Use ISO 8601 text. Timed preferences require explicit offsets, e.g. `2027-01-02T18:00:00+01:00`
or `2027-01-02T17:00:00Z`; API results normalize them to UTC. Date-only stays use `2027-01-03`
and an exclusive end date `2027-01-05`, interpreted in the business timezone. Do not mix a date
and date-time in one interval. End must follow start. Native Sheets date serial numbers and
offset-free date-times are rejected. `quantity` is required and positive; `party_size` is
optional and positive. Both are integers ≤1,000,000. Preferences are optional plain text ≤4000
characters. Status is exactly one of WAITING, OFFERED, ACCEPTED, DECLINED, CANCELLED, EXPIRED,
INACTIVE. These describe external entries, not an implemented matching/state-transition workflow.

## API surface

All routes below require a selected tenant and owner/admin membership. All mutating methods
require the existing session CSRF header and trusted Origin. Authentication cookies are HttpOnly.

| Method | `/api/v1/integrations` path | Purpose |
| --- | --- | --- |
| GET / PUT / DELETE | `/sheets` | Status / validate and configure `{spreadsheet_id}` / forget configuration |
| GET | `/sheets/health` | Validate external records and return counts |
| GET | `/sheets/customers` | Validated external customers |
| GET | `/sheets/customers/{customer_id}` | Stable customer lookup |
| GET | `/sheets/waiting-list` | Validated external entries |
| GET | `/sheets/waiting-list/{entry_id}` | Stable entry lookup |
| POST | `/sheets/waiting-list/{entry_id}/status` | `{status, idempotency_key}`; status cell only |

No data operation accepts a file ID. Configuration binds the current tenant, connection ID and
Google subject; changing Google accounts requires configuring the file again. Knowing another
tenant's file ID does not grant access to its credentials; Google still enforces file permissions.
Businesses should use distinct files and Google sharing permissions where isolation is required.
Google OAuth consent alone cannot prove a shared file belongs exclusively to one business.

Updates use the [values update API](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/update)
with RAW input. Stable IDs are resolved at write time rather than retained row numbers. Supply
a unique 8–100 character operation key and reuse it for retries. Reusing a key for different data
is rejected. A completed receipt does not reapply a later externally reverted status. After a
detected external row movement, the receipt is REVIEW and cannot be retried under that key.
See the [concurrency limitation](architecture-pivot.md#known-external-consistency-boundary).
DELETE only forgets configuration; it never deletes the Google file. Disconnect revokes the
Google grant and disables access; configuration remains visible for subsequent reconnection.

Tests and real-account smoke commands are in [integration smoke testing](integration-smoke-test.md).
