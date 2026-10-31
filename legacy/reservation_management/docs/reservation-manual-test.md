# Manual multi-industry and real Calendar test

Automated tests use real PostgreSQL and mocked Google HTTP. A real Google write test was not
performed in this session because no configured account/interactive consent was supplied.

1. Start Compose and register/select a business. Open **Reservations**, then **Business settings**.
   Set descriptive business type and IANA timezone; these labels never choose booking rules.
2. Restaurant example: create exclusive resources of type `table` with capacities 2, 4, and 6.
   Create a TIMED dining offering with a 60-minute default, require resources, and map all tables
   as compatible. Create a customer. A party of five should allocate the six-seat table; a second
   overlapping party of five must fail, while a party of two can use another table.
3. Hotel example in a different business: create exclusive `room` resources with guest capacities.
   Create a NIGHTLY offering with configurable check-in 15:00, checkout 11:00, and required room
   type. Select compatible rooms, create a customer, and book multiple nights. Overlapping stays
   on the same room must fail; checkout-day arrival at the configured time should succeed unless
   turnaround extends occupancy past that check-in.
4. Appointment example: create `professional` and `room` resources. Configure a TIMED offering
   requiring one of each and map both. Reserving only the professional must fail; automatic
   allocation should reserve both. A concurrent booking using either occupied asset must fail.
5. Shared example: create a CAPACITY offering with limit three, optionally backed by a SHARED
   resource with capacity three. Book quantity two plus quantity one during the same interval;
   another quantity one must fail. Cancellation must make capacity available immediately.
6. Check list filters, monthly Calendar view, detail/edit/reschedule, completion/no-show, waiting-list
   timed/nightly preferences and deactivation, customer consent, resource/offer deactivation,
   and manager-only business configuration. Use two accounts to confirm isolation.
7. Follow google-setup.md and connect a Google test account through **Settings**. Select a writable
   dedicated test calendar. The scopes from Task 03 already include calendar.events; no additional
   scopes are introduced. Use a disposable test calendar and harmless reservation titles.
8. Create a fresh reservation. Within the scheduler's next cycle it should change from PENDING to
   SYNCED. Compare the event dates/times with that reservation in Google Calendar. Nightly events
   must be all-day spans ending on the exclusive checkout date.
9. Edit/reschedule it. Google must update the same managed event, not create a second copy. Retry
   an already synchronized version; the event count must remain unchanged.
10. Cancel it. Capacity should release immediately in ReSlot, independently of Google. Only its
    managed event should be removed. An unrelated event on that calendar must remain untouched.
11. Temporarily stop the worker (`docker compose stop worker`), save/edit reservations, then
    restart it (`docker compose start worker`). They remain committed and their latest snapshots
    should synchronize after recovery. Stopping Redis/Beat delays dispatch but does not lose the
    PostgreSQL outbox; restart services to resume periodic scans.
12. Remove Google access or calendar writer permission. Saved reservations must remain present
    with a visible sync failure. Restore the same Google account and original calendar access,
    then use Retry. Select a calendar before retrying previously NOT_CONFIGURED bookings.
13. Edit a managed event directly in Google, then change its reservation. Expect CONFLICT, not an
    overwritten external change. Review it manually; this task has no force-overwrite resolver.
    Changing the selected calendar affects new targets and never silently moves existing events.

Record only sanitized statuses and event counts. Never record OAuth tokens, authorization codes,
session cookies, customer emails, or client secrets. The automated suite covers lost responses,
lease expiry, conditional-write races, raw SQL capacity races, and simultaneous HTTP bookings,
which are difficult to reproduce reliably by clicking.
