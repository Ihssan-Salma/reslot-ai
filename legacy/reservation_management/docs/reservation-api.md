# Reservation API

Prefix: `/api/v1/booking`. Every route derives the selected tenant from its authenticated session
and current membership. Never send tenant_id in payloads. Unsafe methods need trusted Origin and
X-CSRF-Token as in the existing frontend client. Owner/admin membership is required for business,
offering, resource, compatibility, and booking-policy configuration; normal members can operate
customers, reservations, and waiting lists.

| Path | Methods / behavior |
| --- | --- |
| `/business` | GET / PUT business_type and IANA timezone |
| `/offerings` | GET paginated list / POST with nested typed policy and requirements |
| `/offerings/{id}` | GET / PUT full validated replacement / DELETE deactivate |
| `/resources` | GET paginated list / POST |
| `/resources/{id}` | GET / PUT / DELETE deactivate |
| `/offering-resources` | GET / POST compatible offering/resource pairs |
| `/offering-resources/{id}` | GET / PUT / DELETE |
| `/offerings/{id}/compatible-resources` | GET active compatible assets |
| `/customers` | GET / POST |
| `/customers/{id}` | GET / PUT / DELETE when unreferenced |
| `/availability` | POST BookingInput; available flag, reason, normalized times, suggested resources |
| `/available-periods` | POST bounded period search; periods, truncated, next_start_at |
| `/reservations` | GET date/status/offering/resource filters / POST |
| `/reservations/{id}` | GET / PUT / DELETE cancel and soft-archive active bookings |
| `/reservations/{id}/resources` | POST explicit resource_ids and optional expected_version |
| `/reservations/{id}/reschedule` | POST a new window, optional resources and expected_version |
| `/reservations/{id}/cancel` | POST cancellation and allocation release |
| `/reservations/{id}/status` | POST PENDING/CONFIRMED/CANCELLED/COMPLETED/NO_SHOW and optional expected_version |
| `/reservations/{id}/retry-sync` | POST requeue latest synchronization version |
| `/waitlist` | GET / POST |
| `/waitlist/{id}` | GET / PUT / DELETE |
| `/synchronizations` | GET safe outbox status; no leases, credentials, or internal snapshots |

List routes use limit/offset (limit up to 500). Reservation filters: start_at/end_at (offset
ISO datetimes), or local_from/local_until (business-local dates with an exclusive upper bound),
status, offering_id, resource_id. End/start windows use overlap semantics [start,end).

Example offering configuration:

```json
{
  "name": "Consultation with meeting room",
  "category": "consulting",
  "booking_mode": "TIMED",
  "min_duration_minutes": 30,
  "max_duration_minutes": 120,
  "policy": {
    "require_resources": true,
    "requirements": [
      {"resource_type": "professional", "required_count": 1},
      {"resource_type": "room", "required_count": 1}
    ]
  }
}
```

Timed creation uses customer_id, offering_id, start_at/end_at with explicit offsets or
local_start_at/local_end_at in the business timezone. Local values must omit offsets; ambiguous
DST wall times require the explicit-offset fields instead. An end can be omitted when a default
duration is configured. Nightly creation uses local_start_date/local_end_date and no timestamps.
Optional resource_ids explicitly assigns multiple compatible assets; omit it for automatic
allocation. Quantity consumes shared/offering capacity; party_size checks asset accommodation.
Never use frontend availability results as a booking guarantee; create/update recheck under locks.

Waitlist inputs use available_from/available_until or local wall times for timed preferences,
and local_start_date/local_end_date for nightly preferences. preferred_resource_id is optional
and must belong to the selected business and offering compatibility map.

Pydantic schemas and all response contracts are also available at `/docs`. API errors are safe
400/403/404/409/422/503 messages; provider details, OAuth tokens, and database diagnostics are not
returned. Google failures appear asynchronously on reservation/outbox status rather than rolling
back the original request.
