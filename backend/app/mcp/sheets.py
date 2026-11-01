from datetime import datetime, time

from sqlalchemy.orm import Session

from app.core.context import TenantContext
from app.integrations.google.http import GoogleHTTP
from app.mcp.schemas import (
    Candidate,
    Candidates,
    CandidatesInput,
    CustomerInput,
    EntryInput,
    Profile,
    ToolResult,
)
from app.services.calendar_reservations import local_time
from app.services.google_sheets import GoogleSheetsService


def candidates(
    db: Session,
    context: TenantContext,
    http: GoogleHTTP,
    args: CandidatesInput,
) -> ToolResult:
    service = GoogleSheetsService(db, context, http)
    customers, entries, _ = service.read_dataset(service.configured_id())
    consent = {row.customer_id: row.notification_consent for row in customers}
    matches = []
    for _, row in entries:
        if row.status != "WAITING" or not consent[row.customer_id]:
            continue
        if row.requested_offering.strip().casefold() != args.offering.strip().casefold():
            continue
        start, end = row.preferred_start, row.preferred_end
        if not isinstance(start, datetime):
            start = local_time(datetime.combine(start, time()), context.tenant.timezone)
            end = local_time(datetime.combine(end, time()), context.tenant.timezone)
        if not (start <= args.start_at and args.end_at <= end):
            continue
        # Omitted capacity is unknown, never unlimited. No capacity arithmetic by an LLM.
        if args.quantity is None or row.quantity is None or row.quantity > args.quantity:
            continue
        if row.party_size is not None and (
            args.party_size is None or row.party_size > args.party_size
        ):
            continue
        matches.append(
            Candidate(
                entry_id=row.entry_id,
                customer_id=row.customer_id,
                party_size=row.party_size,
                quantity=row.quantity,
                requires_review=bool(row.additional_preferences.strip()),
            )
        )
    matches.sort(key=lambda row: row.entry_id)
    return ToolResult(
        category="OK",
        message="Deterministic eligibility; review flagged preferences",
        data=Candidates(candidates=matches),
    )


def customer(
    db: Session,
    context: TenantContext,
    http: GoogleHTTP,
    args: CustomerInput,
) -> ToolResult:
    row = GoogleSheetsService(db, context, http).get_customer(args.customer_id)
    return ToolResult(
        category="OK",
        message="Authorized customer profile",
        data=Profile(**row.model_dump(exclude={"phone"})),
    )


def entry(db: Session, context: TenantContext, http: GoogleHTTP, args: EntryInput) -> ToolResult:
    row = GoogleSheetsService(db, context, http).get_waitlist_entry(args.entry_id)
    return ToolResult(category="OK", message="Untrusted waiting-list preferences", data=row)
