"""Local MCP demonstration. Synthetic data by default; real access requires explicit login."""

import argparse
import asyncio
import json
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from getpass import getpass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import httpx
from dotenv import load_dotenv

from app.mcp.client import GoogleToolsClient
from app.mcp.context import SessionExecutor
from app.mcp.schemas import ToolResult
from app.mcp.server import _build_server, create_server
from app.schemas.automation import CalendarAvailability, CustomerRecord, WaitingListRecord
from app.services.calendar_reservations import normalize_reservation_event


@contextmanager
def synthetic_server() -> Iterator[Any]:
    """Offline fixture only. Never attach this server to application transports."""
    customer = CustomerRecord(
        customer_id="demo-customer",
        full_name="Synthetic Customer",
        email="synthetic@example.com",
        phone=None,
        notification_consent=True,
    )
    entry = WaitingListRecord(
        entry_id="demo-entry",
        customer_id="demo-customer",
        requested_offering="Dinner",
        preferred_start="2027-01-02T17:00:00Z",
        preferred_end="2027-01-02T20:00:00Z",
        party_size=2,
        quantity=1,
        additional_preferences="",
        status="WAITING",
    )
    event = normalize_reservation_event(
        {
            "id": "demo-event",
            "start": {"dateTime": "2027-01-02T17:00:00Z"},
            "end": {"dateTime": "2027-01-02T18:00:00Z"},
            "extendedProperties": {
                "private": {
                    "reslot_kind": "reservation",
                    "reslot_schema": "1",
                    "reslot_reference": "DEMO-1",
                    "reslot_offering": "Dinner",
                    "reslot_quantity": "1",
                    "reslot_party_size": "2",
                }
            },
        },
        "synthetic-calendar",
        "UTC",
    )
    sheets = Mock()
    sheets.read_dataset.return_value = ([customer], [(2, entry)], [])
    sheets.get_customer.return_value = customer
    sheets.get_waitlist_entry.return_value = entry
    calendar = Mock()
    calendar.get_reservation_event.return_value = event
    calendar.check_calendar_availability.return_value = CalendarAvailability(
        calendar_id="synthetic-calendar", available=False, conflicting_event_ids=["demo-event"]
    )

    class SyntheticExecutor:
        def execute(self, operation: Any) -> ToolResult:
            return operation(None, SimpleNamespace(tenant=SimpleNamespace(timezone="UTC")), None)

    with ExitStack() as stack:
        stack.enter_context(patch("app.mcp.sheets.GoogleSheetsService", return_value=sheets))
        stack.enter_context(
            patch("app.mcp.calendar.CalendarReservationService", return_value=calendar)
        )
        yield _build_server(SyntheticExecutor())


async def demonstrate(server: Any, args: argparse.Namespace) -> int:
    async with GoogleToolsClient(server) as client:
        print(
            json.dumps(
                {
                    "server": "ReSlotGoogleTools",
                    "tools": [t["name"] for t in await client.discover()],
                }
            )
        )
        calls = [
            ("get_calendar_event", {"event_id": args.event_id}),
            (
                "get_waitlist_candidates",
                {
                    "offering": args.offering,
                    "start_at": args.start_at,
                    "end_at": args.end_at,
                    "party_size": args.party_size,
                    "quantity": args.quantity,
                },
            ),
            ("get_customer_profile", {"customer_id": args.customer_id}),
        ]
        if args.message_id:
            calls.append(("get_email_message", {"message_id": args.message_id}))
        failed = False
        for name, arguments in calls:
            result = await client.call(name, arguments)
            failed |= result.category not in {"OK", "REVIEW_REQUIRED"}
            # Real account output confirms schema/category without printing contact data or text.
            payload = (
                result.model_dump(mode="json")
                if not args.real
                else {
                    "category": result.category,
                    "message": result.message,
                    "validated_data_fields": list(type(result.data).model_fields)
                    if result.data
                    else [],
                }
            )
            print(json.dumps({"tool": name, "result": payload}, indent=2))
        return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real", action="store_true", help="Use a dedicated, authorized Google test account"
    )
    parser.add_argument(
        "--serve", action="store_true", help="Run the authenticated server over local stdio"
    )
    parser.add_argument("--base-url", default="http://localhost:5173")
    parser.add_argument("--email")
    parser.add_argument("--tenant-id")
    parser.add_argument("--event-id", default="demo-event")
    parser.add_argument("--customer-id", default="demo-customer")
    parser.add_argument("--offering", default="Dinner")
    parser.add_argument("--start-at", default="2027-01-02T17:00:00Z")
    parser.add_argument("--end-at", default="2027-01-02T18:00:00Z")
    parser.add_argument("--party-size", type=int, default=2)
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--message-id")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")

    def run(server: Any) -> int:
        if args.serve:
            server.run(transport="stdio")
            return 0
        return asyncio.run(demonstrate(server, args))

    if not args.real:
        if args.serve or args.message_id:
            parser.error("Standalone stdio and Gmail smoke checks require --real")
        with synthetic_server() as server:
            return run(server)
    if not args.email or not args.tenant_id:
        parser.error("--real requires --email and --tenant-id")
    if not args.base_url.startswith(("http://localhost:", "http://127.0.0.1:", "https://")):
        parser.error("Use a localhost or HTTPS API origin")
    with httpx.Client(
        base_url=args.base_url.rstrip("/"), timeout=60, follow_redirects=False
    ) as http:

        def post(path: str, body: dict[str, Any]) -> None:
            csrf = http.get("/api/v1/auth/csrf")
            csrf.raise_for_status()
            response = http.post(
                "/api/v1" + path,
                json=body,
                headers={
                    "Origin": args.base_url.rstrip("/"),
                    "X-CSRF-Token": csrf.json()["csrf_token"],
                },
            )
            if response.status_code >= 400:
                raise RuntimeError("Authenticated application request failed")

        post("/auth/login", {"email": args.email, "password": getpass("ReSlot password: ")})
        try:
            post("/tenants/switch", {"tenant_id": args.tenant_id})
            token = http.cookies.get("__Host-reslot_session") or http.cookies.get("reslot_session")
            if not token:
                raise RuntimeError("Session unavailable")
            return run(create_server(SessionExecutor.from_session(token)))
        finally:
            post("/auth/logout", {})


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "MCP demonstration failed; check authentication, configuration and resource access.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
