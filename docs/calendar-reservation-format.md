# Generic Calendar reservation events

Calendar remains the reservation source of truth. This app does not create reservations or
modify events. Merely having a reservation-like title never marks an event as a reservation.

Prefer Google's [private extended properties](https://developers.google.com/workspace/calendar/api/guides/extended-properties)
when your existing booking system can write them:

```json
{
  "extendedProperties": {
    "private": {
      "reslot_kind": "reservation",
      "reslot_schema": "1",
      "reslot_reference": "EXT-001",
      "reslot_offering": "Dinner",
      "reslot_quantity": "1",
      "reslot_party_size": "2",
      "reslot_resource": "Window area",
      "reslot_requirements": "Step-free access",
      "reslot_customer_reference": "cust-001"
    }
  }
}
```

Required metadata: kind/schema/reference/offering/quantity. Resource, party size, requirements,
and customer reference are optional. An optional `reslot_tenant_id` must match the authenticated
business UUID. Numeric properties must be positive whole numbers ≤1,000,000; customer references
follow the Sheets stable-ID format. Offerings and resource labels are free text, without
salon/hotel/restaurant-specific domain tables. Old `reslot_reservation` markers require review.

For a manually created event, place exactly one plain JSON block in its description:

```text
[ReSlot Reservation v1]
{"reservation_reference":"EXT-001","offering":"Overnight stay","quantity":1,"party_size":2,"resource_label":"Quiet area","additional_requirements":"","customer_reference":"cust-001"}
[/ReSlot Reservation]
```

Only the documented fields are accepted. Duplicate keys, incomplete or multiple blocks,
unsupported versions, invalid values, or conflicting private/description metadata produce
`classification: review`, with no normalized reservation. Unmarked events produce `ignored`.
These are review flags returned by the API, not a new management/review dashboard.

Timed events use Calendar `start.dateTime` and `end.dateTime`, normally RFC3339 offsets. Explicit
IANA endpoint zones or the configured calendar timezone can resolve offset-free values; ambiguous
or nonexistent local DST times are rejected. All-day events use two ISO dates, with Calendar's
exclusive end date. They normalize to UTC instants at local midnight and retain `all_day: true`;
a DST day need not be 24 hours. Mixed types and nonpositive intervals require review. See the
[Calendar event representation](https://developers.google.com/workspace/calendar/api/v3/reference/events).

`GET /api/v1/integrations/calendar/reservations/{event_id}` only accesses the tenant's selected
calendar and returns classification, source status, review reasons, and (when supported):
calendar_id, event_id, reservation_reference, offering, start_at, end_at, resource_label,
party_size, quantity, additional_requirements, optional customer_reference, and all_day.
Cancellation status is kept separately. Task 05 preserves the last valid normalized snapshot
when later source metadata is incomplete. A cancelled tombstone or event HTTP 404/410 can recover
that snapshot only for the same tenant, selected calendar, event and Google subject. Recovery is
flagged for review with `data_source: snapshot`; missing HTTP resources use source status
`unavailable`, since a 404 alone does not prove deletion. Access denial and outages never trigger
fallback. No valid previous snapshot means no recovery. See [MCP details](mcp.md) and
[actual verification](verification.md). Full provider events are never stored.

`POST /api/v1/integrations/calendar/availability` accepts offset-aware `start_at`, `end_at`
(up to 31 days), and optional `exclude_event_id`. Busy opaque overlapping events block availability,
including unmarked events; cancelled and transparent events do not. Intervals are half-open.
An exclusion must itself be a valid marked reservation from the selected calendar. Invalid busy
intervals produce `requires_review: true`, `available: false`; paging is bounded. This checks
time conflicts, not room/table counts or capacity, and does not reserve/hold a time slot.
